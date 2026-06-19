#!/usr/bin/env python3
"""
JTEX to ASTC Converter
用于转换Gameloft游戏（如狂野飙车9）的jtex纹理格式为ASTC格式

JTEX格式说明:
- 魔数: 9字节 \\x89jtex x\\r\\n
- 文件头: 包含纹理尺寸、格式、mipmap数量等信息
- 纹理数据: 每个mipmap级别都是zstd压缩的ASTC数据

使用方法:
    python jtex2astc.py input.jtex [output.astc]
"""

import struct
import sys
import os
import ctypes

# 加载zstd库
try:
    zstd = ctypes.CDLL('libzstd.so')
except:
    try:
        zstd = ctypes.CDLL('libzstd.so.1')
    except:
        print("错误: 无法加载zstd库")
        sys.exit(1)

# 定义zstd函数原型
zstd.ZSTD_decompress.restype = ctypes.c_size_t
zstd.ZSTD_decompress.argtypes = [
    ctypes.c_void_p,  # dst
    ctypes.c_size_t,  # dstCapacity
    ctypes.c_void_p,  # src
    ctypes.c_size_t   # srcSize
]

zstd.ZSTD_getFrameContentSize.restype = ctypes.c_ulonglong
zstd.ZSTD_getFrameContentSize.argtypes = [
    ctypes.c_void_p,  # src
    ctypes.c_size_t   # srcSize
]

zstd.ZSTD_findFrameCompressedSize.restype = ctypes.c_size_t
zstd.ZSTD_findFrameCompressedSize.argtypes = [
    ctypes.c_void_p,  # src
    ctypes.c_size_t   # srcSize
]

zstd.ZSTD_isError.restype = ctypes.c_uint
zstd.ZSTD_isError.argtypes = [ctypes.c_size_t]

zstd.ZSTD_getErrorName.restype = ctypes.c_char_p
zstd.ZSTD_getErrorName.argtypes = [ctypes.c_size_t]


class JTEXParser:
    """JTEX文件解析器"""
    
    def __init__(self, filepath):
        self.filepath = filepath
        self.data = None
        self.width = 0
        self.height = 0
        self.block_width = 0
        self.block_height = 0
        self.mipmap_count = 0
        self.mipmaps = []  # 每个元素是解压后的mipmap数据
        self.format = 0
        
    def parse(self):
        """解析jtex文件"""
        with open(self.filepath, 'rb') as f:
            self.data = f.read()
        
        # 检查魔数
        magic = self.data[:9]
        if not magic.startswith(b'\x89jtex'):
            print(f"错误: 不是有效的jtex文件，魔数: {magic.hex()}")
            return False
        
        print(f"文件大小: {len(self.data)} 字节")
        
        # 尝试从文件头读取尺寸信息
        self._parse_header()
        
        # 查找所有zstd帧
        self._find_zstd_frames()
        
        if len(self.mipmaps) == 0:
            print("错误: 未找到纹理数据")
            return False
        
        # 如果文件头没有读取到尺寸，根据mipmap大小反推
        if self.width == 0 or self.height == 0:
            self._guess_format()
        
        print(f"纹理尺寸: {self.width} x {self.height}")
        print(f"ASTC块大小: {self.block_width} x {self.block_height}")
        print(f"Mipmap级别: {self.mipmap_count}")
        
        return True
    
    def _parse_header(self):
        """从文件头解析尺寸信息"""
        if len(self.data) < 0x30:
            return
        
        # 偏移0x26的字节: log2(width) + 1
        # 例如: 1024 -> log2(1024)=10 -> 10+1=11
        width_log2 = self.data[0x26] - 1
        if width_log2 >= 6 and width_log2 <= 12:  # 64到4096
            self.width = 1 << width_log2
            # 假设是正方形纹理（狂野飙车9的纹理大多是正方形）
            self.height = self.width
            # 默认块大小是10x10（最常见）
            self.block_width = 10
            self.block_height = 10
    
    def _find_zstd_frames(self):
        """查找所有zstd压缩帧"""
        zstd_magic = struct.pack('<I', 0xFD2FB528)
        
        pos = 0
        while True:
            pos = self.data.find(zstd_magic, pos)
            if pos < 0:
                break
            
            # 获取帧大小
            frame_size = zstd.ZSTD_findFrameCompressedSize(
                self.data[pos:], len(self.data) - pos
            )
            
            if zstd.ZSTD_isError(frame_size):
                pos += 1
                continue
            
            # 获取解压后大小
            decomp_size = zstd.ZSTD_getFrameContentSize(
                self.data[pos:], frame_size
            )
            
            if decomp_size == 0 or decomp_size == 0xFFFFFFFFFFFFFFFF:
                pos += 1
                continue
            
            # 解压数据
            dst = ctypes.create_string_buffer(decomp_size)
            result = zstd.ZSTD_decompress(
                dst, decomp_size, self.data[pos:], frame_size
            )
            
            if zstd.ZSTD_isError(result):
                pos += 1
                continue
            
            # 保存解压后的数据
            self.mipmaps.append(dst.raw[:result])
            
            pos += frame_size
        
        self.mipmap_count = len(self.mipmaps)
    
    def _guess_format(self):
        """根据mipmap大小猜测纹理格式"""
        if len(self.mipmaps) == 0:
            return
        
        # 最大的mipmap（级别0）
        largest_size = len(self.mipmaps[0])
        
        # ASTC每块16字节
        blocks_total = largest_size // 16
        
        # 尝试不同的块大小
        astc_block_sizes = [
            (4, 4), (5, 4), (5, 5), (6, 5), (6, 6),
            (8, 5), (8, 6), (8, 8), (10, 5), (10, 6),
            (10, 8), (10, 10), (12, 10), (12, 12)
        ]
        
        best_match = None
        best_score = -1
        
        for bw, bh in astc_block_sizes:
            # 尝试不同的宽度（2的幂次）
            for w in [64, 128, 256, 512, 1024, 2048, 4096]:
                blocks_w = (w + bw - 1) // bw
                
                if blocks_total % blocks_w == 0:
                    blocks_h = blocks_total // blocks_w
                    h = blocks_h * bh
                    
                    # 检查高度是否合理
                    if h < 64 or h > 4096:
                        continue
                    
                    # 检查宽高比是否合理
                    ratio = max(w, h) / min(w, h)
                    if ratio > 4:
                        continue
                    
                    # 验证其他mipmap级别是否匹配
                    score = 0
                    valid = True
                    
                    for i, mip_data in enumerate(self.mipmaps[1:], 1):
                        mip_size = len(mip_data)
                        mip_w = w >> i
                        mip_h = h >> i
                        
                        if mip_w < 1 or mip_h < 1:
                            valid = False
                            break
                        
                        mip_blocks_w = (mip_w + bw - 1) // bw
                        mip_blocks_h = (mip_h + bh - 1) // bh
                        expected_size = mip_blocks_w * mip_blocks_h * 16
                        
                        if mip_size == expected_size:
                            score += 1
                        else:
                            valid = False
                            break
                    
                    if valid:
                        # 评分：优先选择尺寸是2的幂次的，以及更大的尺寸
                        power_of_two = (w & (w - 1)) == 0 and (h & (h - 1)) == 0
                        size_score = w + h
                        
                        total_score = score * 100 + (100 if power_of_two else 0) + size_score
                        
                        if total_score > best_score:
                            best_score = total_score
                            # 使用实际的纹理尺寸（2的幂次），而不是块对齐后的尺寸
                            best_match = (w, w, bw, bh)  # 假设是正方形纹理
        
        if best_match:
            self.width, self.height, self.block_width, self.block_height = best_match
        else:
            # 如果没找到匹配，使用默认值
            self.width = 1024
            self.height = 1024
            self.block_width = 10
            self.block_height = 10
    
    def save_as_astc(self, output_path, all_mipmaps=False):
        """保存为ASTC格式（ARM标准格式）
        
        Args:
            output_path: 输出文件路径
            all_mipmaps: 是否保存所有mipmap级别（默认False，只保存基础级别）
        """
        if self.width == 0 or self.height == 0:
            print("错误: 未知纹理尺寸")
            return False
        
        # ARM标准ASTC文件头格式 (16字节):
        # magic: 4 bytes 0x5CA1AB13 (小端序存储)
        # blockdim_x: 1 byte
        # blockdim_y: 1 byte
        # blockdim_z: 1 byte
        # xsize: 3 bytes (uint24, little-endian)
        # ysize: 3 bytes (uint24, little-endian)
        # zsize: 3 bytes (uint24, little-endian)
        
        def uint24_le(value):
            """将整数转换为3字节小端序"""
            return struct.pack('<I', value)[:3]
        
        with open(output_path, 'wb') as f:
            # 魔数 (0x5CA1AB13 小端序)
            f.write(struct.pack('<I', 0x5CA1AB13))
            
            # 块尺寸
            f.write(bytes([self.block_width, self.block_height, 1]))
            
            # 尺寸 (3字节小端序)
            f.write(uint24_le(self.width))
            f.write(uint24_le(self.height))
            f.write(uint24_le(1))
            
            # 写入mipmap数据
            if all_mipmaps:
                # 保存所有mipmap级别（非标准，某些工具可能不支持）
                for i, mip_data in enumerate(self.mipmaps):
                    f.write(mip_data)
                    print(f"  Mipmap {i}: {len(mip_data)} 字节")
            else:
                # 只保存基础级别（标准ASTC格式）
                f.write(self.mipmaps[0])
                print(f"  基础级别: {len(self.mipmaps[0])} 字节")
        
        print(f"\n已保存ASTC文件: {output_path}")
        return True
    
    def save_as_png(self, output_path):
        """保存为PNG格式（需要texture2ddecoder和Pillow库）
        
        Args:
            output_path: 输出文件路径
        """
        try:
            import texture2ddecoder
            from PIL import Image
        except ImportError as e:
            print(f"错误: 缺少依赖库 ({e})")
            print("请安装: pip install texture2ddecoder pillow")
            return False
        
        if self.width == 0 or self.height == 0:
            print("错误: 未知纹理尺寸")
            return False
        
        # 解码ASTC
        tex_data = self.mipmaps[0]
        rgba_data = texture2ddecoder.decode_astc(
            tex_data, self.width, self.height, 
            self.block_width, self.block_height
        )
        
        # 保存为PNG
        # 注意：texture2ddecoder返回的是BGRA格式，需要转换成RGBA
        img = Image.frombytes('RGBA', (self.width, self.height), rgba_data)
        r, g, b, a = img.split()
        img = Image.merge('RGBA', (b, g, r, a))
        img.save(output_path)
        
        print(f"  输出尺寸: {self.width} x {self.height}")
        print(f"\n已保存PNG文件: {output_path}")
        return True


def main():
    if len(sys.argv) < 2:
        print("用法: python jtex2astc.py <input.jtex> [output]")
        print()
        print("说明:")
        print("  - 输出文件后缀为 .png 时自动转PNG格式")
        print("  - 输出文件后缀为 .astc 或不指定时输出ASTC格式")
        print()
        print("示例:")
        print("  python jtex2astc.py texture.jtex              # 输出 texture.astc")
        print("  python jtex2astc.py texture.jtex out.astc     # 输出 ASTC格式")
        print("  python jtex2astc.py texture.jtex out.png      # 输出 PNG格式")
        sys.exit(1)
    
    input_file = sys.argv[1]
    if len(sys.argv) >= 3:
        output_file = sys.argv[2]
    else:
        output_file = os.path.splitext(input_file)[0] + '.astc'
    
    if not os.path.exists(input_file):
        print(f"错误: 文件不存在: {input_file}")
        sys.exit(1)
    
    print(f"正在解析: {input_file}")
    print("=" * 50)
    
    parser = JTEXParser(input_file)
    if not parser.parse():
        print("解析失败")
        sys.exit(1)
    
    print()
    print("正在转换...")
    
    # 根据输出文件后缀判断格式
    ext = os.path.splitext(output_file)[1].lower()
    
    if ext == '.png':
        success = parser.save_as_png(output_file)
    else:
        success = parser.save_as_astc(output_file)
    
    if success:
        print("\n转换完成!")
    else:
        print("\n转换失败!")
        sys.exit(1)


if __name__ == '__main__':
    main()