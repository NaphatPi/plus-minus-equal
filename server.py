#!/usr/bin/env python

import asyncio

from websockets.asyncio.server import serve


async def hello(websocket):
    while True:
        name = await websocket.recv()

        print("recieved:", name)

        await websocket.send(name[::-1])
        print(f">>> {name[::-1]}")


async def main():
    async with serve(hello, "localhost", 8765):
        await asyncio.get_running_loop().create_future()  # run forever


if __name__ == "__main__":
    asyncio.run(main())
