from dataclasses import dataclass
import math

@dataclass
class Request:
    device_id : int
    lpa       : int
    size_bytes: int
    size_pages: int
    req_type  : str

    def isReadReq(self) -> bool:
        return self.req_type == "R"

    def isWriteReq(self) -> bool:
        return self.req_type == "W"


class TraceReader:
    def __init__(self, trace_file, page_size):
        self.trace_file = trace_file
        self.page_size  = page_size
        self.fp         = open(trace_file, "r")

    def read_request(self):
        line = self.fp.readline()
        if not line:
            return None

        line = line.strip()
        if len(line) == 0:
            return None

        parts = line.split()
        if len(parts) != 4:
            raise ValueError(f"Invalid trace format: {line}")

        device_id  = int(parts[0])
        lpa        = int(parts[1])
        size_bytes = int(parts[2])
        req_type   = parts[3]

        #if size_bytes % self.page_size != 0:
        #    raise ValueError(f"Unaligned request size: {size_bytes}")

        size_pages = math.ceil(size_bytes / self.page_size)
        if size_pages == 0:
            size_pages = 1

        req = Request(
            device_id  = device_id,
            lpa        = lpa,
            size_bytes = size_bytes,
            size_pages = size_pages,
            req_type   = req_type
        )
        return req

    def close(self):
        self.fp.close()

