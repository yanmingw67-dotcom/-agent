import threading

import psutil


class ResourceSampler:
    def __init__(self):
        self.process = psutil.Process()
        self.stop = threading.Event()
        self.cpu_peak = 0.0
        self.rss_peak = self.process.memory_info().rss
        self.thread = threading.Thread(target=self.sample, daemon=True)

    def sample(self):
        self.process.cpu_percent()
        while not self.stop.wait(0.05):
            self.cpu_peak = max(self.cpu_peak, self.process.cpu_percent())
            self.rss_peak = max(self.rss_peak, self.process.memory_info().rss)

    def start(self):
        self.thread.start()

    def finish(self):
        self.stop.set()
        self.thread.join(timeout=1)
        return {
            "sampled_peak_cpu_percent": round(self.cpu_peak, 2),
            "sampled_peak_rss_bytes": self.rss_peak,
            "resource_sample_interval_ms": 50,
        }
