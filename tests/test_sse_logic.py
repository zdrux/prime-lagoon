import asyncio
import json
import time
import threading
from typing import List

# Mocking the classes/logic from admin.py to verify it works in isolation
class PollManager:
    def __init__(self):
        self.subscribers: List[asyncio.Queue] = []
        self.is_running = False
        self._lock = asyncio.Lock()
        self.loop = None

    async def subscribe(self) -> asyncio.Queue:
        if not self.loop:
            self.loop = asyncio.get_running_loop()
        q = asyncio.Queue()
        self.subscribers.append(q)
        print(f"DEBUG: New subscriber. Total: {len(self.subscribers)}")
        return q

    def unsubscribe(self, q: asyncio.Queue):
        if q in self.subscribers:
            self.subscribers.remove(q)
            print(f"DEBUG: Subscriber removed. Total: {len(self.subscribers)}")

    def broadcast(self, data):
        if not self.loop:
            print("DEBUG: No loop set for broadcast")
            return
        print(f"DEBUG: Broadcasting: {data}")
        for q in self.subscribers:
            self.loop.call_soon_threadsafe(q.put_nowait, data)

    async def start(self, mock_poller_func):
        async with self._lock:
            if self.is_running:
                print("DEBUG: Poller already running, joining existing run.")
                return
            self.is_running = True
            
            def run_wrapper():
                try:
                    mock_poller_func(progress_callback=self.broadcast)
                    self.broadcast({"type": "done"})
                except Exception as e:
                    self.broadcast({"type": "error", "message": str(e)})
                finally:
                    self.is_running = False
                    print("DEBUG: Poller thread finished.")

            print("DEBUG: Starting poller thread.")
            threading.Thread(target=run_wrapper, daemon=True).start()

async def event_generator(poll_manager, mock_poller_func, heartbeat_timeout=1.0):
    """Refactored generator logic for testing (faster heartbeat)."""
    queue = await poll_manager.subscribe()
    try:
        if not poll_manager.is_running:
            await poll_manager.start(mock_poller_func)
        
        while True:
            try:
                msg = await asyncio.wait_for(queue.get(), timeout=heartbeat_timeout)
                if isinstance(msg, dict) and msg.get('type') == 'done':
                    yield f"data: {json.dumps(msg)}\n\n"
                    break
                yield f"data: {json.dumps(msg)}\n\n"
            except asyncio.TimeoutError:
                yield ": heartbeat\n\n"
    finally:
        poll_manager.unsubscribe(queue)

async def test_main():
    pm = PollManager()
    
    def slow_poller(progress_callback):
        time.sleep(0.5)
        progress_callback({"type": "cluster_start", "cluster": "cluster-1"})
        time.sleep(2.5) # This should trigger heartbeats (timeout=1.0)
        progress_callback({"type": "cluster_start", "cluster": "cluster-2"})
        time.sleep(0.5)
        # 'done' will be sent by the manager

    print("--- Starting Client 1 ---")
    gen1 = event_generator(pm, slow_poller, heartbeat_timeout=1.0)
    
    # Run client 1 for a bit
    print("Client 1 receiving:")
    async for msg in gen1:
        print(f"C1 RECEIVED: {msg.strip()}")
        if "cluster-1" in msg:
            print("--- Starting Client 2 (joining mid-way) ---")
            # We'll start client 2 concurrently
            asyncio.create_task(run_client_2(pm, slow_poller))

async def run_client_2(pm, slow_poller):
    gen2 = event_generator(pm, slow_poller, heartbeat_timeout=1.0)
    print("Client 2 receiving:")
    async for msg in gen2:
        print(f"C2 RECEIVED: {msg.strip()}")

if __name__ == "__main__":
    asyncio.run(test_main())
