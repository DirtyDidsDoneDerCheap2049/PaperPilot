"""Rebuild derived local retrieval data without modifying research records."""
import argparse
import asyncio
import json
import sqlite3
import sys
import time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from src.knowledge.paper_retrieval import PaperRetriever,index_library


class ReadOnlyDatabase:
    def __init__(self,path):
        self.conn=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)
        self.conn.row_factory=sqlite3.Row
    def fetchall(self,sql,params=()):
        return [dict(row) for row in self.conn.execute(sql,params)]


async def main(workspace):
    root=Path(workspace).resolve()
    db=ReadOnlyDatabase(root/'db/ai_reader.db')
    retriever=PaperRetriever(root)
    started=time.monotonic()
    async def progress(title,completed,total):
        if completed%10==0 or completed==total:
            print(json.dumps({'completed':completed,'total':total,'elapsed_seconds':round(time.monotonic()-started,1)},ensure_ascii=False),flush=True)
    try:
        result=await index_library(db,retriever,progress)
        result['elapsed_seconds']=round(time.monotonic()-started,2)
        print(json.dumps(result,ensure_ascii=False,indent=2),flush=True)
    finally:
        retriever.close();db.conn.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--workspace',type=Path,required=True,help='Existing SQLite workspace; research DB opened read-only')
    asyncio.run(main(parser.parse_args().workspace))
