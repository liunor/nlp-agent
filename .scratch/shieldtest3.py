import asyncio

async def child():
    try:
        await asyncio.sleep(1)
    finally:
        print("child exiting")

async def waiter():
    print("waiter enter", flush=True)
    t = asyncio.create_task(child())
    g = asyncio.gather(t, return_exceptions=True)
    print("before shield", flush=True)
    try:
        await asyncio.shield(g)
        print("shield returned normally", flush=True)
    except asyncio.CancelledError:
        print("shield caught", flush=True)
        try:
            await g
            print("g settled", flush=True)
        except BaseException as e:
            print("g await raised", type(e).__name__, repr(e), flush=True)
        raise
    print("waiter end", flush=True)

async def main():
    w = asyncio.create_task(waiter())
    await asyncio.sleep(0.02)
    print("cancelling", flush=True)
    w.cancel()
    try:
        await w
    except BaseException as e:
        print("main got", type(e).__name__, repr(e), flush=True)

asyncio.run(main())
