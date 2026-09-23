#include "sqlite3.h"
#include "json.hpp"
#include <filesystem>
#include <fstream>
#include <iostream>
#include <memory>
#include <stdexcept>
#include <string>
#include <vector>
#ifdef _WIN32
#include <windows.h>
#include <shellapi.h>
#else
#include <sys/file.h>
#include <fcntl.h>
#include <unistd.h>
#endif

using json = nlohmann::json;
namespace fs = std::filesystem;
#ifdef READER_MYSQL
#include "mysql_connection.hpp"
#endif

// A process-level lock prevents a second daemon from recovering live jobs.
struct OwnerLock {
#ifdef _WIN32
    HANDLE handle;
    explicit OwnerLock(const fs::path& path) : handle(CreateFileW(path.wstring().c_str(), GENERIC_READ | GENERIC_WRITE, 0, nullptr, OPEN_ALWAYS, FILE_ATTRIBUTE_NORMAL, nullptr)) {
        if (handle == INVALID_HANDLE_VALUE) throw std::runtime_error("workspace_already_open");
    }
    ~OwnerLock() { CloseHandle(handle); }
#else
    int handle;
    explicit OwnerLock(const fs::path& path) : handle(open(path.c_str(), O_CREAT | O_RDWR, 0600)) {
        if (handle < 0) throw std::runtime_error("lock_open_failed");
        if (flock(handle, LOCK_EX | LOCK_NB)) { close(handle); throw std::runtime_error("workspace_already_open"); }
    }
    ~OwnerLock() { close(handle); }
#endif
    OwnerLock(const OwnerLock&) = delete;
    OwnerLock& operator=(const OwnerLock&) = delete;
};

class Store {
    std::unique_ptr<sqlite3, decltype(&sqlite3_close)> db{nullptr, sqlite3_close};
    bool mysql_backend=false;
#ifdef READER_MYSQL
    std::unique_ptr<MySQLConnection> mysql;
#endif
public:
    explicit Store(const std::string& path) {
        const char* storage=std::getenv("READER_STORAGE");
        mysql_backend=storage&&std::string(storage)=="mysql";
        if(mysql_backend) {
#ifdef READER_MYSQL
            mysql=std::make_unique<MySQLConnection>();
            exec("CREATE TABLE IF NOT EXISTS jobs(id VARCHAR(255) PRIMARY KEY, session_id VARCHAR(255) NOT NULL, request_key VARCHAR(128) NOT NULL UNIQUE, payload LONGTEXT NOT NULL, status VARCHAR(32) NOT NULL, token BIGINT NOT NULL DEFAULT 0, result LONGTEXT, error TEXT, created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6), updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6), _seq BIGINT UNSIGNED NOT NULL AUTO_INCREMENT UNIQUE, active_session VARCHAR(255) GENERATED ALWAYS AS (CASE WHEN status IN ('queued','running','cancel_requested') THEN session_id ELSE NULL END) STORED, UNIQUE KEY one_active_session(active_session), CHECK(status IN ('queued','running','cancel_requested','cancelled','interrupted','succeeded','failed'))) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin");
            exec("CREATE TABLE IF NOT EXISTS events(seq BIGINT UNSIGNED PRIMARY KEY AUTO_INCREMENT, job_id VARCHAR(255) NOT NULL, body LONGTEXT NOT NULL, created_at DATETIME(6) DEFAULT CURRENT_TIMESTAMP(6), KEY events_job(job_id,seq)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin");
            exec("UPDATE jobs SET status='interrupted', error='Application stopped before completion; retry may incur API charges', token=token+1, updated_at=CURRENT_TIMESTAMP WHERE status IN ('running','cancel_requested')");
            return;
#else
            throw std::runtime_error("mysql_client_not_built");
#endif
        }
        sqlite3* raw = nullptr;
        int rc = sqlite3_open(path.c_str(), &raw);
        db.reset(raw);
        if (rc != SQLITE_OK) throw std::runtime_error("database_open_failed");
        sqlite3_busy_timeout(db.get(), 5000);
        exec("PRAGMA journal_mode=WAL");
        exec("PRAGMA synchronous=FULL");
        exec("CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, session_id TEXT NOT NULL, request_key TEXT NOT NULL UNIQUE, payload TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('queued','running','cancel_requested','cancelled','interrupted','succeeded','failed')), token INTEGER NOT NULL DEFAULT 0, result TEXT, error TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)");
        exec("CREATE UNIQUE INDEX IF NOT EXISTS one_active_session ON jobs(session_id) WHERE status IN ('queued','running','cancel_requested')");
        exec("CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL, body TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP)");
        exec("CREATE INDEX IF NOT EXISTS events_job ON events(job_id, seq)");
        exec("PRAGMA user_version=1");
        exec("UPDATE jobs SET status='interrupted', error='Application stopped before completion; retry may incur API charges', token=token+1, updated_at=CURRENT_TIMESTAMP WHERE status IN ('running','cancel_requested')");
    }
    json query(const std::string& sql, const std::vector<json>& args={}) {
#ifdef READER_MYSQL
        if(mysql_backend)return mysql->query(sql,args);
#endif
        sqlite3_stmt* raw = nullptr;
        if (sqlite3_prepare_v2(db.get(), sql.c_str(), -1, &raw, nullptr) != SQLITE_OK) throw std::runtime_error("database_prepare_failed");
        std::unique_ptr<sqlite3_stmt, decltype(&sqlite3_finalize)> stmt(raw, sqlite3_finalize);
        for (size_t i=0; i<args.size(); ++i) {
            if (args[i].is_number_integer()) sqlite3_bind_int64(raw, int(i+1), args[i].get<long long>());
            else if (args[i].is_null()) sqlite3_bind_null(raw, int(i+1));
            else { auto value=args[i].get<std::string>(); sqlite3_bind_text(raw,int(i+1),value.c_str(),int(value.size()),SQLITE_TRANSIENT); }
        }
        json rows=json::array(); int rc;
        while ((rc=sqlite3_step(raw))==SQLITE_ROW) {
            json row=json::object();
            for(int i=0;i<sqlite3_column_count(raw);++i) {
                std::string key=sqlite3_column_name(raw,i);
                if(sqlite3_column_type(raw,i)==SQLITE_NULL) row[key]=nullptr;
                else if(sqlite3_column_type(raw,i)==SQLITE_INTEGER) row[key]=sqlite3_column_int64(raw,i);
                else row[key]=reinterpret_cast<const char*>(sqlite3_column_text(raw,i));
            }
            rows.push_back(row);
        }
        if(rc!=SQLITE_DONE) throw std::runtime_error(rc==SQLITE_CONSTRAINT ? "conflict" : "database_write_failed");
        return rows;
    }
    void exec(const std::string& sql, const std::vector<json>& args={}) { query(sql,args); }
    json get(const std::string& id) {
        auto rows=query("SELECT * FROM jobs WHERE id=?",{id});
        if(rows.empty()) throw std::runtime_error("not_found");
        return rows[0];
    }
    json command(const json& q) {
        auto op=q.at("op").get<std::string>();
        if(op=="ping") return {{"engine",mysql_backend?"cpp17-mysql":"cpp17-sqlite"},{"protocol",1}};
        if(op=="get") return get(q.at("id"));
        if(op=="list") return query("SELECT * FROM jobs ORDER BY rowid DESC LIMIT 100");
        if(op=="events") return query("SELECT * FROM events WHERE job_id=? AND seq>? ORDER BY seq LIMIT 200",{q.at("id"),q.value("after",0LL)});
        if(op=="submit") {
            std::string payload=q.at("payload").dump();
            auto old=query("SELECT * FROM jobs WHERE request_key=?",{q.at("request_key")});
            if(!old.empty()) {
                if(old[0]["payload"]!=payload || old[0]["session_id"]!=q.at("session_id")) throw std::runtime_error("idempotency_conflict");
                return old[0];
            }
            if(query("SELECT id FROM jobs WHERE status IN ('queued','running','cancel_requested')").size()>=20) throw std::runtime_error("queue_full");
            exec("INSERT INTO jobs(id,session_id,request_key,payload,status) VALUES(?,?,?,?,'queued')",{q.at("id"),q.at("session_id"),q.at("request_key"),payload});
            return get(q.at("id"));
        }
        if(op=="claim") {
            if(!query("SELECT id FROM jobs WHERE status IN ('running','cancel_requested')").empty()) return nullptr;
            auto rows=query("SELECT id FROM jobs WHERE status='queued' ORDER BY rowid LIMIT 1");
            if(rows.empty()) return nullptr;
            auto id=rows[0]["id"];
            exec("UPDATE jobs SET status='running',token=token+1,updated_at=CURRENT_TIMESTAMP WHERE id=? AND status='queued'",{id});
            return get(id);
        }
        auto id=q.at("id").get<std::string>(); auto job=get(id);
        if(op=="cancel") {
            exec("UPDATE jobs SET status=CASE status WHEN 'queued' THEN 'cancelled' ELSE 'cancel_requested' END,updated_at=CURRENT_TIMESTAMP WHERE id=? AND status IN ('queued','running')",{id});
            return get(id);
        }
        if(op=="retry") {
            if(job["status"]!="interrupted" && job["status"]!="failed" && job["status"]!="cancelled") throw std::runtime_error("not_retryable");
            exec("UPDATE jobs SET status='queued',error=NULL,result=NULL,updated_at=CURRENT_TIMESTAMP WHERE id=?",{id});
            return get(id);
        }
        if(q.at("token")!=job["token"]) throw std::runtime_error("stale_attempt");
        if(op=="finish") {
            auto status=q.at("status").get<std::string>();
            if(status!="succeeded" && status!="failed" && status!="cancelled" && status!="interrupted") throw std::runtime_error("invalid_status");
            if(job["status"]==status) return job; // Lost reply: safe to repeat completion.
            if(job["status"]!="running" && job["status"]!="cancel_requested") throw std::runtime_error("terminal_job");
            if(job["status"]=="cancel_requested") status="cancelled";
            exec("UPDATE jobs SET status=?,result=?,error=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",{status,q.value("result",json::object()).dump(),q.value("error",std::string()),id});
            return get(id);
        }
        if(op=="event") {
            if(job["status"]!="running" && job["status"]!="cancel_requested") throw std::runtime_error("terminal_job");
            exec("INSERT INTO events(job_id,body) VALUES(?,?)",{id,q.at("body").dump()});
            return {{"saved",true}};
        }
        throw std::runtime_error("unknown_operation");
    }
};

int main(int argc,char** argv) {
    try {
        if(argc!=2) throw std::runtime_error("usage: reader_tasks <database>");
        fs::path path;
#ifdef _WIN32
        int count=0; auto wide=CommandLineToArgvW(GetCommandLineW(),&count);
        if(!wide || count!=2) throw std::runtime_error("invalid_arguments");
        path=wide[1]; LocalFree(wide);
#else
        path=fs::u8path(argv[1]);
#endif
        fs::create_directories(path.parent_path());
        OwnerLock lock(path.u8string()+".lock"); Store store(path.u8string());
        std::string line;
        while(std::getline(std::cin,line)) {
            json reply;
            try {
                if(line.size()>8*1024*1024) throw std::runtime_error("request_too_large");
                auto q=json::parse(line);
                store.exec("BEGIN IMMEDIATE");
                try { auto data=store.command(q); store.exec("COMMIT"); reply={{"ok",true},{"data",data}}; }
                catch(...) { store.exec("ROLLBACK"); throw; }
            } catch(const std::exception& e) { reply={{"ok",false},{"error",e.what()}}; }
            std::cout<<reply.dump()<<std::endl;
        }
    } catch(const std::exception& e) { std::cerr<<e.what()<<std::endl; return 1; }
}
