"""CRX → ZIP：去掉 CRX 头，取出内层 ZIP。移植自 lixian.online 的 crx.ts。"""

from __future__ import annotations

import struct

from app.offline.errors import OfflineError


def crx_to_zip(data: bytes) -> bytes:
    if len(data) < 16:
        return data

    if data[:2] == b"PK":
        return data

    zip_offset = 0
    if data[:4] == b"Cr24":
        (version,) = struct.unpack_from("<I", data, 4)
        if version == 3:
            (header_size,) = struct.unpack_from("<I", data, 8)
            zip_offset = 12 + header_size
        elif version == 2:
            pub_key_len, sig_len = struct.unpack_from("<II", data, 8)
            zip_offset = 16 + pub_key_len + sig_len
        else:
            raise OfflineError(f"不支持的 CRX 版本: {version}")
    else:
        found = data[:1024].find(b"PK")
        if found < 0:
            return data
        zip_offset = found

    if zip_offset >= len(data):
        raise OfflineError("ZIP 数据偏移量超出文件范围")
    inner = data[zip_offset:]
    if len(inner) < 4 or inner[:2] != b"PK":
        raise OfflineError("提取的数据不是有效的 ZIP 格式")
    return inner
