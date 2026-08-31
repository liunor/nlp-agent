import asyncio

async def cleaner():
    print("cleaner start")
    await asyncio.sleep(0.1)
    print("cleaner end")

async def worker():
    try:
        await asyncio.sleep(10)
    finally:
        print("finally start")
        await cleaner()
        print("finally end")

async def main():
    t = asyncio.create_task(worker())
    await asyncio.sleep(0.05)
    t.cancel()
    try:
        await t
    except BaseException as e:
        print("main got", type(e), e)

asyncio.run(main())
