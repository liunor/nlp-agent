import asyncio

async def child():
    try:
        await asyncio.sleep(1)
    except asyncio.CancelledError:
        await asyncio.sleep(0)
        raise

async def waiter():
    print("waiter enter")
    t = asyncio.create_task(child())
    await asyncio.sleep(0)
    t.cancel()
    g = asyncio.gather(t, return_exceptions=True)
    print("before shield", flush=True)
    try:
        await asyncio.shield(g)
        print("shield returned normally")
    except asyncio.CancelledError:
        print("shielfreshness", flush=True)
        await g
        print("g settled")
        raise

async def main():
    w = asyncio.create_task(waiter())
    await asyncio.sleep(0.02)  # waiter at shield
    w.cancel()
    try:
        await w
    except BaseException as e:
        pass

asyncio.run(main())
