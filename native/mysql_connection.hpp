#pragma once
#include <mysql.h>
#include <cstdlib>
#include <memory>
#include <vector>
#include <string>
#include <stdexcept>

class MySQLConnection {
    std::unique_ptr<MYSQL, decltype(&mysql_close)> connection{mysql_init(nullptr),mysql_close};
    static std::string env(const char* key,const char* fallback="") {
        auto value=std::getenv(key); return value?value:fallback;
    }
public:
    MySQLConnection() {
        if(!connection) throw std::runtime_error("mysql_init_failed");
        unsigned int timeout=10;
        mysql_options(connection.get(),MYSQL_OPT_CONNECT_TIMEOUT,&timeout);
        mysql_options(connection.get(),MYSQL_OPT_READ_TIMEOUT,&timeout);
        mysql_options(connection.get(),MYSQL_OPT_WRITE_TIMEOUT,&timeout);
        mysql_options(connection.get(),MYSQL_SET_CHARSET_NAME,"utf8mb4");
        auto database=env("MYSQL_DATABASE"),user=env("MYSQL_USER"),password=env("MYSQL_PASSWORD");
        if(database.empty()||user.empty()) throw std::runtime_error("mysql_configuration_missing");
        auto ca=env("MYSQL_SSL_CA");
        if(!ca.empty()) {
            auto mode=SSL_MODE_VERIFY_IDENTITY;
            mysql_options(connection.get(),MYSQL_OPT_SSL_CA,ca.c_str());
            mysql_options(connection.get(),MYSQL_OPT_SSL_MODE,&mode);
        }
        if(!mysql_real_connect(connection.get(),env("MYSQL_HOST","127.0.0.1").c_str(),user.c_str(),password.c_str(),database.c_str(),std::stoul(env("MYSQL_PORT","3306")),nullptr,0))
            throw std::runtime_error("mysql_connection_failed");
        query("SET time_zone='+00:00'");
        query("SET SESSION TRANSACTION ISOLATION LEVEL READ COMMITTED");
        auto locked=query("SELECT GET_LOCK(?,0) AS acquired",{"reader:"+database});
        if(locked.empty()||locked[0]["acquired"]!=1)throw std::runtime_error("workspace_already_open");
    }
    json query(std::string sql,const std::vector<json>& args={}) {
        if(sql=="BEGIN IMMEDIATE")sql="START TRANSACTION";
        if(args.empty()&&(sql=="START TRANSACTION"||sql=="COMMIT"||sql=="ROLLBACK")) {
            if(mysql_real_query(connection.get(),sql.data(),sql.size()))throw std::runtime_error("mysql_transaction_failed");
            return json::array();
        }
        for(size_t pos=0;(pos=sql.find("rowid",pos))!=std::string::npos;pos+=4)sql.replace(pos,5,"_seq");
        std::unique_ptr<MYSQL_STMT,decltype(&mysql_stmt_close)> statement(mysql_stmt_init(connection.get()),mysql_stmt_close);
        auto stmt=statement.get();
        if(!stmt||mysql_stmt_prepare(stmt,sql.data(),sql.size()))throw std::runtime_error("mysql_prepare_failed");
        if(mysql_stmt_param_count(stmt)!=args.size())throw std::runtime_error("mysql_parameter_count");
        std::vector<MYSQL_BIND> input(args.size());
        std::vector<std::string> strings(args.size());
        std::vector<long long> numbers(args.size());
        for(size_t i=0;i<args.size();++i) {
            if(args[i].is_null())input[i].buffer_type=MYSQL_TYPE_NULL;
            else if(args[i].is_number_integer()) {
                numbers[i]=args[i].get<long long>();input[i].buffer_type=MYSQL_TYPE_LONGLONG;input[i].buffer=&numbers[i];
            } else {
                strings[i]=args[i].get<std::string>();input[i].buffer_type=MYSQL_TYPE_STRING;
                input[i].buffer=strings[i].data();input[i].buffer_length=strings[i].size();
            }
        }
        if(!input.empty()&&mysql_stmt_bind_param(stmt,input.data()))throw std::runtime_error("mysql_bind_failed");
        bool update_length=true;
        mysql_stmt_attr_set(stmt,STMT_ATTR_UPDATE_MAX_LENGTH,&update_length);
        if(mysql_stmt_execute(stmt))throw std::runtime_error(mysql_stmt_errno(stmt)==1062?"conflict":"mysql_execution_failed");
        if(mysql_stmt_store_result(stmt))throw std::runtime_error("mysql_result_failed");
        std::unique_ptr<MYSQL_RES,decltype(&mysql_free_result)> metadata(mysql_stmt_result_metadata(stmt),mysql_free_result);
        json rows=json::array(); if(!metadata)return rows;
        auto count=mysql_num_fields(metadata.get());auto fields=mysql_fetch_fields(metadata.get());
        std::vector<MYSQL_BIND> output(count);
        std::vector<std::vector<char>> buffers(count);
        std::vector<unsigned long> lengths(count);
        auto nulls=std::make_unique<bool[]>(count);
        for(unsigned int i=0;i<count;++i) {
            if(fields[i].max_length>8*1024*1024)throw std::runtime_error("mysql_result_too_large");
            buffers[i].resize(fields[i].max_length+1);
            output[i].buffer_type=MYSQL_TYPE_STRING;output[i].buffer=buffers[i].data();output[i].buffer_length=buffers[i].size();
            output[i].length=&lengths[i];output[i].is_null=&nulls[i];
        }
        if(mysql_stmt_bind_result(stmt,output.data()))throw std::runtime_error("mysql_result_bind_failed");
        int code;
        while((code=mysql_stmt_fetch(stmt))==0) {
            json row=json::object();
            for(unsigned int i=0;i<count;++i) {
                if(nulls[i])row[fields[i].name]=nullptr;
                else {
                    std::string value(buffers[i].data(),lengths[i]);
                    auto type=fields[i].type;
                    if(type==MYSQL_TYPE_LONGLONG||type==MYSQL_TYPE_LONG||type==MYSQL_TYPE_SHORT||type==MYSQL_TYPE_TINY)row[fields[i].name]=std::stoll(value);
                    else row[fields[i].name]=value;
                }
            }
            rows.push_back(row);
        }
        if(code!=MYSQL_NO_DATA)throw std::runtime_error("mysql_fetch_failed");
        return rows;
    }
};
