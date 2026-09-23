from urllib.parse import urlsplit
from starlette.responses import JSONResponse

class LocalOnlyMiddleware:
    def __init__(self,app): self.app=app
    async def __call__(self,scope,receive,send):
        if scope['type'] not in {'http','websocket'}:
            return await self.app(scope,receive,send)
        headers={k.decode().lower():v.decode() for k,v in scope.get('headers',[])}
        host=headers.get('host','')
        name=urlsplit('http://'+host).hostname
        origin=headers.get('origin')
        allowed=name in {'127.0.0.1','localhost','::1','testserver'}
        if origin and urlsplit(origin).netloc!=host: allowed=False
        if scope['type']=='http' and scope.get('method') not in {'GET','HEAD','OPTIONS'} and headers.get('x-reader-client')!='desktop': allowed=False
        if not allowed:
            if scope['type']=='websocket':
                await send({'type':'websocket.close','code':1008})
            else:
                await JSONResponse({'detail':'仅允许桌面本地访问'},status_code=403)(scope,receive,send)
            return
        await self.app(scope,receive,send)
