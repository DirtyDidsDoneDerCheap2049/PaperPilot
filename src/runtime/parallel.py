"""Bounded independent work, ordered results, and joined cancellation."""
import asyncio


async def ordered_map(items, operation, limit=3):
    items = list(items)
    results = [None] * len(items)
    cursor = 0

    async def consume():
        nonlocal cursor
        while cursor < len(items):
            index = cursor
            cursor += 1  # No await between allocation and advancement.
            results[index] = await operation(items[index])

    tasks = [asyncio.create_task(consume()) for _ in range(min(max(1, limit), len(items)))]
    try:
        await asyncio.gather(*tasks)
    except BaseException:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise
    return results
