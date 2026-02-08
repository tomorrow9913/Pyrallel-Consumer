import orjson
from concurrent.futures import ThreadPoolExecutor

def batch_deserialize(messages):
    success_batch, failed_batch = [], []
    for msg in messages:
        try:
            val = msg.value()
            if val: success_batch.append(orjson.loads(val))
        except Exception as e:
            failed_batch.append((val, e))
    return success_batch, failed_batch

class BatchWorker:
    def __init__(self, thread_pool_size=4):
        self.executor = ThreadPoolExecutor(max_workers=thread_pool_size)

    async def process(self, loop, messages, processor_func, topic):
        success, _ = await loop.run_in_executor(self.executor, batch_deserialize, messages)
        if success:
            await processor_func(topic, success)