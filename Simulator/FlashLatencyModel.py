"""
Latency is typically composed of:
1. Command Overhead - time to send command and address
2. Execution Latency - time to execute operation on the chip
3. Data Transfer Latency - time to transfer data between controller and flash (depends on bus speed and width)
"""

from enum import Enum, auto
from dataclasses import dataclass
import math
from SSDConfig import SSDConfig

class OperationType(Enum):
    READ  = auto()
    WRITE = auto()
    ERASE = auto()

@dataclass
class FlashLatencyParams:
    """Latency parameters in nanoseconds"""    
    # READ Latency
    read_command_time   : int         # ns - time to send READ command
    read_execution_time : int         # ns - time to read data from cell
    # WRITE Latency  
    write_command_time  : int         # ns - time to send WRITE command
    write_execution_time: int         # ns - time to write data to cell
    # ERASE Latency
    erase_command_time  : int         # ns - time to send ERASE command
    erase_execution_time: int         # ns - time to erase block


class FlashLatencyModel:
    """
    Calculate latency for Flash operations
    Supported features:
    - Single operation latency
    """
    
    def __init__(self, config: SSDConfig):
        self.params = FlashLatencyParams(
            read_command_time      = config.read_command_time,
            read_execution_time    = config.read_execution_time,
            write_command_time     = config.write_command_time,
            write_execution_time   = config.write_execution_time,
            erase_command_time     = config.erase_command_time,
            erase_execution_time   = config.erase_execution_time,
        )

        self.bus_width_bytes       = config.bus_width
        self.bus_speed_mts         = config.speed_mts

        # Calculate time per byte on bus (nanoseconds)
        # 1 transfer = bus_width bytes
        # time_per_transfer = 1 / (bus_speed_mts * 10^6) * 10^9 = 1000 / bus_speed_mts
        self.transfer_time_per_byte = (
            (1000 * self.bus_width_bytes) / (self.bus_speed_mts * self.bus_width_bytes)
        )  # ns per byte
        
        # Time per page transfer (4KB typical)
        self.page_transfer_overhead = 1000  # ~1μs for addressing/control
    
    # get read latency in nanoseconds
    def get_read_latency(self, transfer_size: int) -> int:
        transfer_time = self.calculate_transfer_time(transfer_size)
        return self.params.read_command_time + self.params.read_execution_time + transfer_time
    
    # get write latency in nanoseconds
    def get_write_latency(self, transfer_size: int) -> int:
        transfer_time = self.calculate_transfer_time(transfer_size)
        return self.params.write_command_time + self.params.write_execution_time + transfer_time
    
    # get erase latency in nanoseconds
    def get_erase_latency(self) -> int:      
        return self.params.erase_command_time + self.params.erase_execution_time
    
    # calculate transfer time based on bus speed and width
    def calculate_transfer_time(self, num_bytes: int) -> int:
        # 1 transfer = bus_width_bytes
        # time_per_transfer = 1 / (speed_mts * 10^6) * 10^9 ns = 1000 / speed_mts ns
        time_per_transfer_ns = 1000 / self.bus_speed_mts  # ns per transfer
        num_transfers = math.ceil(num_bytes / self.bus_width_bytes)
        # Add overhead for addressing/control
        return (num_transfers * time_per_transfer_ns) + self.page_transfer_overhead
    
    # Calculate latency for an operation (READ/WRITE/ERASE) in nanoseconds
    def get_operation_latency(self, op_type: OperationType, size_bytes: int = None) -> int:
        if op_type == OperationType.READ:
            return self.get_read_latency(size_bytes)
        elif op_type == OperationType.WRITE:
            return self.get_write_latency(size_bytes)
        elif op_type == OperationType.ERASE:
            return self.get_erase_latency()
        else:
            raise Exception(f"Unknown operation type: {op_type}")
    
    # Calculate latency in microseconds
    def get_latency_us(self, op_type: OperationType, size_bytes: int) -> float:
        latency_ns = self.get_operation_latency(op_type, size_bytes)
        return latency_ns / 1000.0  # Convert ns to us
    
    # Calculate latency in milliseconds
    def get_latency_ms(self, op_type: OperationType, size_bytes: int) -> float:
        latency_ns = self.get_operation_latency(op_type, size_bytes)
        return latency_ns / 1000000.0  # Convert ns to ms
