from dataclasses import dataclass
from FlashLatencyModel import FlashLatencyModel, OperationType
from DRAMLatencyModel import DRAMLatencyModel
from SSDConfig import SSDConfig

@dataclass
class TimedOperation:
    """Record of an operation with timing information"""
    #op_id             : int
    op_type           : OperationType
    ppa               : int
    lun_id            : int
    size_bytes        : int = 0
    timestamp_start_ns: int = 0
    timestamp_end_ns  : int = 0
    latency_ns        : int = 0


class TimingTracker:
    def __init__(self, config: SSDConfig, flash_latency_model: FlashLatencyModel, dram_latency_model: DRAMLatencyModel):
        self.latency_model      = flash_latency_model
        self.dram_latency_model = dram_latency_model
        #self.op_counter         = 0
        self.current_time_ns    = 0
        self.controller_ready_time_ns = 0
        self.lun_available_time = {}

        self.pages_per_block     = config.pages_per_block
        self.blocks_per_plane    = config.blocks_per_plane
        self.planes_per_die      = config.planes_per_die
        self.dies_per_chip       = config.dies_per_chip
        self.chips_per_channel   = config.chips_per_channel
        
        self.stats = {
            'total_operations': 0,
            'read_operations' : 0,
            'write_operations': 0,
            'erase_operations': 0,
            'total_latency_ns': 0,
            'read_latency_ns' : 0,
            'write_latency_ns': 0,
            'erase_latency_ns': 0,
            'total_dram_latency_ns': 0,
            'dram_read_operations': 0,
            'dram_write_operations': 0,
        }

    def record_dram_access(self, is_write: bool, count: int = 1):
        """Record DRAM access latency and update controller ready time"""
        latency = self.dram_latency_model.get_write_latency() if is_write else self.dram_latency_model.get_read_latency()
        total_latency = latency * count
        self.controller_ready_time_ns += total_latency
        self.stats['total_dram_latency_ns'] += total_latency
        if is_write:
            self.stats['dram_write_operations'] += count
        else:
            self.stats['dram_read_operations'] += count
        # Also update current_time_ns because DRAM access takes time even if no flash op is running
        self.current_time_ns = max(self.current_time_ns, self.controller_ready_time_ns)

    def lun_id_from_block_id(self, block_id: int) -> int:
        plane_id = block_id // self.blocks_per_plane
        plane_id //= self.planes_per_die
        die = plane_id % self.dies_per_chip
        plane_id //= self.dies_per_chip
        chip = plane_id % self.chips_per_channel
        plane_id //= self.chips_per_channel
        channel = plane_id

        lun_id = channel
        lun_id = lun_id * self.chips_per_channel + chip
        lun_id = lun_id * self.dies_per_chip + die
        return lun_id

    def lun_id_from_ppa(self, ppa: int) -> int:
        block_id = ppa // self.pages_per_block
        return self.lun_id_from_block_id(block_id)

    def record_operation(self, op_type: OperationType, ppa: int, size_bytes: int = 0) -> TimedOperation:
        latency_ns = self.latency_model.get_operation_latency(op_type, size_bytes)
        lun_id = self.lun_id_from_ppa(ppa)
        # Operation starts only when both LUN and Controller are ready
        start_ns = max(self.lun_available_time.get(lun_id, 0), self.controller_ready_time_ns)
        end_ns = start_ns + latency_ns

        op = TimedOperation(
            #op_id              = self.op_counter,
            op_type            = op_type,
            ppa                = ppa,
            lun_id             = lun_id,
            size_bytes         = size_bytes,
            timestamp_start_ns = start_ns,
            timestamp_end_ns   = end_ns,
            latency_ns         = latency_ns
        )
        #self.op_counter += 1
        #self.operations.append(op)
        self.lun_available_time[lun_id] = end_ns
        self.current_time_ns = max(self.current_time_ns, end_ns)
        return op

    def record_block_operation(self, op_type: OperationType, block_id: int, size_bytes: int = 0) -> TimedOperation:
        ppa = block_id * self.pages_per_block
        return self.record_operation(op_type, ppa, size_bytes)
    
    def record_read(self, ppa: int, size_bytes: int) -> TimedOperation:
        op = self.record_operation(OperationType.READ, ppa, size_bytes)
        latency_ns = op.latency_ns
        # Update stats
        self.stats['total_operations'] += 1
        self.stats['read_operations']  += 1
        self.stats['total_latency_ns'] += latency_ns
        self.stats['read_latency_ns']  += latency_ns
        return op

    def record_write(self, ppa: int, size_bytes: int) -> TimedOperation:
        op = self.record_operation(OperationType.WRITE, ppa, size_bytes)
        latency_ns = op.latency_ns
        # Update stats
        self.stats['total_operations'] += 1
        self.stats['write_operations'] += 1
        self.stats['total_latency_ns'] += latency_ns
        self.stats['write_latency_ns'] += latency_ns
        return op

    def record_erase(self, block_id: int) -> TimedOperation:
        op = self.record_block_operation(OperationType.ERASE, block_id)
        latency_ns = op.latency_ns
        # Update stats
        self.stats['total_operations'] += 1
        self.stats['erase_operations'] += 1
        self.stats['total_latency_ns'] += latency_ns
        self.stats['erase_latency_ns'] += latency_ns
        return op
    
    def get_average_read_latency_us(self) -> float:
        if self.stats['read_operations'] == 0:
            return 0
        return self.stats['read_latency_ns'] / self.stats['read_operations'] / 1000.0
    
    def get_average_write_latency_us(self) -> float:
        if self.stats['write_operations'] == 0:
            return 0
        return self.stats['write_latency_ns'] / self.stats['write_operations'] / 1000.0
    
    def get_average_erase_latency_ms(self) -> float:
        if self.stats['erase_operations'] == 0:
            return 0
        return self.stats['erase_latency_ns'] / self.stats['erase_operations'] / 1000000.0
    
    def get_total_time_us(self) -> float:
        return self.current_time_ns / 1000.0
    
    def get_total_time_ms(self) -> float:
        return self.current_time_ns / 1000000.0
    
    def print_statistics(self):       
        print(f"\n--- AVERAGE LATENCIES ---")
        print(f"READ:               {self.get_average_read_latency_us():.2f} µs")
        print(f"WRITE:              {self.get_average_write_latency_us():.2f} µs")
        print(f"ERASE:              {self.get_average_erase_latency_ms():.3f} ms")
        
        print(f"\n--- LATENCY ---")
        print(f"Latency:              {self.get_total_time_ms():.3f} ms")
        
        print("\n" + "="*70)
