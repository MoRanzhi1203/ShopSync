<p align="center">
  <img src="shopsync_data/assets/app.ico" width="80" alt="ShopSync">
</p>

<h1 align="center">ShopSync — 商品分组水印工具</h1>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.9+-blue.svg" alt="Python">
  <img src="https://img.shields.io/badge/PyQt-5.15+-green.svg" alt="PyQt5">
  <img src="https://img.shields.io/badge/Pillow-9.0+-orange.svg" alt="Pillow">
  <img src="https://img.shields.io/badge/platform-Windows-lightgrey.svg" alt="Platform">
  <img src="https://img.shields.io/badge/license-MIT-yellow.svg" alt="License">
</p>

---

## 📖 简介 / Introduction

**ShopSync** 是一款基于 PyQt5 的桌面工具，专为电商商品图片管理设计。支持批量导入、MD5 去重、自动压缩、自定义水印、随机分组合成以及一键批量导出。

> A PyQt5-based desktop tool for e-commerce product image management. Features batch import with deduplication, customizable watermarking, randomized grouping, and one-click batch export.

### 核心功能 / Features

- 🗂️ **批量导入** — 按文件夹导入商品图片，自动 MD5 去重 + 智能压缩（>2MB）
- 🎨 **水印管理** — 支持 PNG 水印，可调节位置、缩放、旋转、透明度
- 🎲 **随机分组** — 多颜色分组随机抽取合成，每组 16-20 张
- 📦 **批量导出** — 一键将二层分组合成导出为商品图

---

## 📸 界面预览 / Screenshots

<p align="center">
  <em>（截图待补充 / Screenshots coming soon）</em>
</p>

| 导入图片 | 水印配置 | 分组管理 |
|:---:|:---:|:---:|
| ![import](docs/screenshots/import.png) | ![watermark](docs/screenshots/watermark.png) | ![group](docs/screenshots/group.png) |

---

## 🚀 快速开始 / Quick Start

### 方式一：源码运行

```bash
# 1. 克隆仓库
git clone https://github.com/yourname/ShopSync.git
cd ShopSync

# 2. 安装依赖
pip install -r requirements.txt

# 3. 运行
python pyqt_product_group_watermark_tool.py
```

### 方式二：直接使用打包版

下载 `dist/ShopSync.zip`，解压后双击 `ShopSync.exe` 即可运行，无需安装 Python 环境。

---

## 📦 依赖 / Dependencies

| 包名 | 版本 | 用途 |
|------|------|------|
| [PyQt5](https://pypi.org/project/PyQt5/) | ≥ 5.15 | GUI 框架 |
| [Pillow](https://pypi.org/project/Pillow/) | ≥ 9.0 | 图片处理 |
| [pywin32](https://pypi.org/project/pywin32/) | ≥ 300 | Windows API（打包用） |

```bash
pip install PyQt5 Pillow pywin32
```

---

## 🏗️ 项目结构 / Project Structure

```
ShopSync/
├── pyqt_product_group_watermark_tool.py   # 主入口 / Main entry + UI
├── ShopSync.spec                           # PyInstaller 打包配置
├── app_resources.qrc                       # Qt 资源描述文件
├── resources_rc.py                         # Qt 资源编译输出
├── README.md
│
├── features/                               # 业务逻辑模块 / Feature modules
│   ├── __init__.py
│   ├── utils.py                            # 公共工具（路径/文件/图片处理）
│   ├── import_dedup.py                     # 导入 & MD5 去重
│   ├── product_grouping.py                 # 分组抽取 & 合成
│   ├── batch_export.py                     # 批量导出
│   └── watermark_config.py                 # 水印配置管理
│
├── shopsync_data/                          # 运行时数据 / Runtime data
│   ├── internal_library/                   # 内部图库（按颜色/类型分组）
│   │   └── md5_index.json                  # MD5 去重索引
│   ├── second_layer/                       # 二层合成输出
│   ├── export_images/                      # 批量导出目录
│   ├── watermark_library/                  # 水印资源库
│   │   ├── images/                         # 水印 PNG 文件
│   │   ├── configs/                        # 水印配置 JSON
│   │   └── wm_settings.json                # 水印应用设置
│   ├── assets/                             # 程序资源（图标/预览底图）
│   └── app_settings.json                   # 用户设置持久化
│
└── dist/                                   # 打包输出 / Distribution
    └── ShopSync/
        ├── ShopSync.exe
        └── shopsync_data/
```

---

## 🔄 工作流程 / Workflow

```
┌──────────────┐      ┌──────────────┐      ┌──────────────┐
│ 1) 导入 Import │ ───▶ │ 2) 水印 WM    │ ───▶ │ 3) 分组 Group  │
│ 去重 + 压缩    │      │ 参数 + 配置   │      │ 抽取 + 导出   │
└──────────────┘      └──────────────┘      └──────────────┘
```

1. **导入**：将商品图片按文件夹导入，自动去重和压缩，存入内部图库
2. **水印**：选择水印图片，调节参数，保存配置，按分组开关应用到对应颜色
3. **分组**：设置输出数量，随机抽取合成，可暂停/中断/取消；最终批量导出

---

## 🧩 关键模块 / Modules

### 主窗口 `MainWindow` (pyqt_product_group_watermark_tool.py)

三标签页应用，核心类：

| 类 | 说明 |
|---|------|
| `WatermarkConfig` | 水印配置 dataclass |
| `ThumbnailWorker` | 异步缩略图生成（QThread） |
| `ImagePreviewDialog` | 图片预览弹窗 |
| `GroupSortManagerDialog` | 分组排序管理 |
| `OverlayPreviewLabel` | 带水印拖拽的实时预览控件 |

### `features/utils.py` — 共享工具

| 函数 | 说明 |
|------|------|
| `ensure_dir(path)` | 递归创建目录 |
| `load_json(path, default)` / `save_json(path, obj)` | JSON 读写 |
| `is_image_file(path)` | 判断支持的图片格式 |
| `md5_of_file(path)` | 计算文件 MD5 |
| `list_images(folder)` | 递归列出图片 |
| `compress_image_to_under_size(src, dst, max_bytes)` | 压缩至目标大小 |
| `apply_watermark(img, wm, cfg)` | 水印合成 |
| `pil_to_qpixmap(img)` | PIL → QPixmap 转换 |
| `rotate_keep_scale(img, angle)` | 旋转并裁切透明边 |

### `features/import_dedup.py`

- 遍历源文件夹的一级子目录作为颜色分组
- MD5 与索引比对去重
- >2MB 图片自动压缩（先降画质，再降分辨率）
- 暂停 / 中断（保留进度）/ 取消（回滚已创建文件）

### `features/product_grouping.py`

- 从各颜色分组随机抽取，按分组水印开关决定是否加水印
- 输出至二层目录，支持暂停 / 中断 / 取消

### `features/batch_export.py`

- 勾选二层分组，统一应用水印配置
- 校验每组 16-20 张，输出 JPEG（quality=95）

---

## ⚙️ 配置说明 / Configuration

| 文件 | 格式 | 说明 |
|------|------|------|
| `md5_index.json` | `{md5: relative_path}` | 去重索引 |
| `app_settings.json` | JSON | 用户偏好（路径记忆、分组排序） |
| `wm_settings.json` | `{wm_file: {rotate, opacity}}` | 水印参数 |
| `configs/*.json` | `WatermarkConfig` | 水印配置模板 |

---

## 🔧 打包 / Build

```bash
# 安装 PyInstaller
pip install pyinstaller

# 使用项目 spec 文件打包
pyinstaller ShopSync.spec
```

输出在 `dist/ShopSync/`，包含：
- `ShopSync.exe` — 可执行文件
- `shopsync_data/` — 首次运行自动生成的运行时数据

---

## ⚠️ 注意事项 / Notes

1. 水印仅支持 **PNG 格式**，建议带透明通道
2. 超过 2MB 的图片导入时自动压缩为 JPEG
3. 二层合成每组建议 **16-20 张**，否则导出时告警
4. MD5 去重基于 `internal_library` 的相对路径
5. 取消操作**自动回滚**已创建的文件

---

## 📄 License

MIT © [Your Name]
