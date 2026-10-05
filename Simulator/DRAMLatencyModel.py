from SSDConfig import SSDConfig

class DRAMLatencyModel:
    def __init__(self, config: SSDConfig):
        self.read_latency_ns  = config.dram_read_latency_ns
        self.write_latency_ns = config.dram_write_latency_ns

    def get_read_latency(self) -> int:
        return self.read_latency_ns

    def get_write_latency(self) -> int:
        return self.write_latency_ns
