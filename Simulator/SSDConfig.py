import json
from dataclasses import dataclass

@dataclass
class SSDConfig:
    # SSD Structure Config
    n_channel: int
    chips_per_channel: int
    dies_per_chip: int
    planes_per_die: int
    blocks_per_plane: int
    pages_per_block: int
    page_size: int
    bus_width: int
    speed_mts: int

    # GC/WL/Translation Config
    gc_wl_reserved_ratio: float
    gc_threshold: float
    gc_target: float
    wl_threshold: int
    translation_reserved_ratio: float

    # Cache and Mapping Config
    cmt_capacity: int
    cmt_ctp_entry_size: int
    cmt_mapping_entry_size: int
    gtd_translation_entry_size: int
    ftp_mapping_entry_size: int

    # Metadata Config
    flash_data: str
    blockList_metadata: str
    data_free_blockID_per_plane_metadata: str
    translation_free_blockID_per_plane_metadata: str
    gc_wl_free_blockID_per_plane_metadata: str
    active_data_blockID_per_plane_metadata: str
    active_translation_blockID_per_plane_metadata: str
    active_gc_wl_blockID_per_plane_metadata: str
    current_plane_metadata: str
    gtd_data: str

    # Flash Latency Parameters (in nanoseconds)
    read_command_time: int
    read_execution_time: int
    write_command_time: int
    write_execution_time: int
    erase_command_time: int
    erase_execution_time: int
    # DRAM Latency Parameters (in nanoseconds)
    dram_read_latency_ns: int
    dram_write_latency_ns: int
    rGTD_GROUP: int = 1

    @staticmethod
    def get_SSDConfig_from_dict(d: dict[str, any]) -> 'SSDConfig':
        return SSDConfig(
            n_channel                                     = int(d["n_channel"]),
            chips_per_channel                             = int(d["chips_per_channel"]),
            dies_per_chip                                 = int(d["dies_per_chip"]),
            planes_per_die                                = int(d["planes_per_die"]),
            blocks_per_plane                              = int(d["blocks_per_plane"]),
            pages_per_block                               = int(d["pages_per_block"]),
            page_size                                     = int(d["page_size"]),
            gc_wl_reserved_ratio                          = float(d["gc_wl_reserved_ratio"]),
            gc_threshold                                  = float(d["gc_threshold"]),
            gc_target                                     = float(d["gc_target"]),
            wl_threshold                                  = int(d["wl_threshold"]),
            translation_reserved_ratio                    = float(d["translation_reserved_ratio"]),
            cmt_capacity                                  = int(d["cmt_capacity"]),
            cmt_ctp_entry_size                            = int(d["cmt_ctp_entry_size"]),
            cmt_mapping_entry_size                        = int(d["cmt_mapping_entry_size"]),
            gtd_translation_entry_size                    = int(d["gtd_translation_entry_size"]),
            ftp_mapping_entry_size                        = int(d["ftp_mapping_entry_size"]),
            bus_width                                     = int(d["bus_width"]),
            speed_mts                                     = int(d["speed_mts"]),
            read_command_time                             = int(d["read_command_time"]),
            read_execution_time                           = int(d["read_execution_time"]),
            write_command_time                            = int(d["write_command_time"]),
            write_execution_time                          = int(d["write_execution_time"]),
            erase_command_time                            = int(d["erase_command_time"]),
            erase_execution_time                          = int(d["erase_execution_time"]),
            dram_read_latency_ns                          = int(d["dram_read_latency_ns"]),
            dram_write_latency_ns                         = int(d["dram_write_latency_ns"]),
            flash_data                                    = d["flash_data"],
            blockList_metadata                            = d["blockList_metadata"],
            data_free_blockID_per_plane_metadata          = d["data_free_blockID_per_plane_metadata"],
            translation_free_blockID_per_plane_metadata   = d["translation_free_blockID_per_plane_metadata"],
            gc_wl_free_blockID_per_plane_metadata         = d["gc_wl_free_blockID_per_plane_metadata"],
            active_data_blockID_per_plane_metadata        = d["active_data_blockID_per_plane_metadata"],
            active_translation_blockID_per_plane_metadata = d["active_translation_blockID_per_plane_metadata"],
            active_gc_wl_blockID_per_plane_metadata       = d["active_gc_wl_blockID_per_plane_metadata"],
            current_plane_metadata                        = d["current_plane_metadata"],
            gtd_data                                      = d["gtd_data"],
            rGTD_GROUP                                    = int(d.get("rGTD_GROUP", d.get("rgtd_group", 1)))
        )

    @staticmethod
    def from_files(struct_path, gc_wl_path, cache_path, timing_path, meta_path):
        combined_dict = {}
        for path in [struct_path, gc_wl_path, cache_path, timing_path, meta_path]:
            with open(path, 'r') as f:
                combined_dict.update(json.load(f))
        return SSDConfig.get_SSDConfig_from_dict(combined_dict)
