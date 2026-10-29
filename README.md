# Notepad View

Notepad View 是一款面向 **Windows 10/11 x64** 的桌面编辑与阅读工具，基于 Python、PySide6 和 Scintilla 构建。支持文本与代码编辑、Markdown 预览、多文档分类管理，以及 PDF、Office、电子书和图片查看。

**文本默认以源码单栏打开。** 启动软件、新建或打开文件、恢复工作区、切换文本标签时，均进入源码页；需要时手动切换双栏或预览，不保存显示模式。图片、PDF、Office、电子书和图表使用各自的专用界面。

> 本文对应当前源码。已发布安装包的功能以对应版本发布说明为准。macOS 构建仍处于准备阶段，尚未在真实 Mac 上验证。

## 主要功能

| 功能 | 支持内容 |
| --- | --- |
| 文本与代码 | 语法高亮、行号、矩形列选择、编码转换、独立撤销与重做 |
| Markdown | 实时预览、标题目录、双栏定位和带主题样式的 HTML 导出 |
| 双栏浏览 | 支持的文本类型可同步滚动、同步文字选区 |
| 查找替换 | 右上角悬浮面板、选区带入、大小写／全字／正则匹配 |
| 格式化与修复 | JSON、JSONL、XML、SQL 格式化，JSON/XML 保守修复与差异确认 |
| 多文档工作区 | 文件分类、标签切换、拖放打开、草稿缓存和会话恢复 |
| 文档阅读 | PDF、Word、Excel、其他 Office 格式与电子书 |
| 图片与图表 | 部分图片编辑，思维导图与流程图编辑、导出 |
| 界面设置 | NV、浅色、深色、护眼主题，侧栏左右切换与宽度调整 |

## 安装与使用

安装后通过 **帮助 → 使用帮助** 或按 `F1`，可在独立的只读窗口中离线阅读本文，不影响正在编辑的文档。帮助内容直接读取随程序分发的 `README.md`。

### Windows 安装版

运行 `NotepadView-1.0.0-Setup.exe`，选择目录完成安装。安装包包含 Python、Qt 及阅读转换运行时，无需单独安装这些组件。

- 默认安装到当前用户的 `%LOCALAPPDATA%\Programs\NotepadView`。
- 支持桌面和开始菜单快捷方式。
- 为部分文本格式注册“打开方式”候选，不修改已有默认打开程序。
- 首次安装应选择空目录；升级可选择已有的有效安装目录。

### Windows 便携版

解压 `NotepadView-1.0.0-Windows-x64-portable.zip`，运行 `NotepadView.exe`。移动时请保留整个解压目录。

便携版使用相同的用户工作区缓存，并不意味着所有配置都保存在程序目录。

### 卸载

在 Windows 设置的“已安装的应用”中卸载，或使用安装目录中的 `uninstall.ps1`。卸载前请关闭应用。

卸载仅删除安装清单内的程序文件，保留用户文档、工作区缓存及界面偏好。

## 文本编辑与预览

### 默认视图

- 启动、新建、打开、恢复会话和切换文本标签时，默认显示源码单栏，忽略以前保存的视图设置。
- 使用 `Ctrl+1`、`Ctrl+2`、`Ctrl+3`，或设置菜单，手动选择源码、双栏、预览。
- 手动开启的视图仅用于当前操作，不作为其他文件或下次启动的默认模式。
- 在仅预览状态执行文本格式操作时，回到源码单栏。
- 图片、PDF、Office、电子书和图表继续使用专用界面，不强制显示二进制源码。

### 文本类型与编辑能力

支持 Markdown、TXT/日志、JSON/JSONL、YAML、XML、HTML、CSS/SCSS、JavaScript/TypeScript、SQL、Python、PowerShell、INI/TOML、CSV/TSV 等类型。未知后缀及无后缀文件会尝试文本识别；无法可靠解码的二进制文件不会强制作为文本打开。

- **Markdown**：支持标题、列表、待办、表格、引用、代码高亮、图片及链接。Markdown 中的 HTML 按文本显示，不执行脚本。暂不支持公式和 Mermaid 渲染。
- **HTML**：预览可执行文档内脚本，并可能加载在线资源，请仅预览可信内容。
- **格式化**：JSON、JSONL、XML、SQL 提供类型对应的格式化；优先处理选区，否则处理全文。其他类型暂不提供通用格式化。
- **修复**：JSON/XML 支持有限、保守的语法修复，先展示修改和差异，确认后应用；失败保留原文，不自动保存原文件。
- **脚本运行**：Windows 下可在独立终端运行 Python 和 PowerShell 文件，执行前先保存。打包版运行 Python 脚本仍需可用的项目或系统 Python。
- **编码**：支持 UTF-8、UTF-8 BOM、UTF-16 等编码重读和转换；ANSI 使用本机代码页。转换后手动保存才写入磁盘，不能表示的字符会阻止保存。

文本采用同目录临时文件原子替换保存。HTML 导出不内嵌图片，分享时需要一并提供相关图片资源。

### 双栏同步

手动开启双栏后，Markdown、纯文本和代码／配置文件支持源码与预览双向联动：

- **同步滚动**：Markdown 按内容块定位，其他支持的文本按行定位。
- **同步选区**：任意一侧选择文字时，另一侧显示对应选区；取消选择时同步清除，并保留操作侧焦点。
- **Markdown 选区**：按渲染后的可见文字对应，不包含被渲染隐藏的标记。

单栏模式停用联动。HTML 不参与双栏同步，PDF、Office、电子书等专用阅读界面也不参与。

### 大文本处理

为减少打开、粘贴、连续删除及标签切换时的界面停顿，当前实现包括：

- 缓存文本类型识别结果，减少未修改文档的重复扫描。
- 大文本预览、Markdown 目录解析和字词统计在后台处理，丢弃过期结果。
- 复用最近两个文本预览的页面节点与同步映射，内容、路径、语言或主题变化后更新缓存。
- 代码预览分块更新，复用未改动的节点；编辑后使旧映射失效，防止旧预览选区覆盖新的源码选择。
- 单栏不建立双栏选区映射，减少额外计算和内存占用。

这些优化不截断正文或撤销历史。较大文档的预览和同步映射仍需处理时间，实际响应取决于文件内容、大小和运行环境。

### 查找替换

按 `Ctrl+F` 调出当前编辑栏或预览栏右上角的悬浮面板。

- 有选中文字时，将选区带入查找框并保持打开；双栏模式按当前焦点读取选区。
- 无选区时，切换面板打开或关闭；`Esc` 可收起面板。
- 拖动面板左边缘调整宽度，本次运行期间保留宽度。
- 输入框右侧的 `Aa`、`ab`、`.*` 分别控制大小写、全字和正则匹配。
- 支持上一个、下一个、替换和全部替换；全部替换可一步撤销。

正则替换支持捕获组，例如 `\1` 和 `\g<名称>`。复杂表达式有超时限制，失败时保留原文。

## 文件阅读与图片处理

| 文件类型 | 支持范围与限制 |
| --- | --- |
| PDF | Qt 原生只读查看、翻页、页码跳转、缩放和适应窗口，不提供编辑或保存 |
| DOCX | 查看正文、表格、基本格式及内嵌图片，不提供编辑和保存 |
| XLSX | 查看单元格、公式文本和工作表；不执行或重新计算公式 |
| DOC、XLS、PPT/PPTX、ODT/ODS/ODP、RTF | 通过 LibreOffice 转换为只读 PDF 预览 |
| EPUB | 章节阅读、目录、缩放和位置恢复；阅读背景与文字随主题切换，浅色主题使用柔和浅灰背景；禁用书内脚本及远程内容 |
| MOBI、AZW3、FB2 | 通过 Calibre 转换为 EPUB 阅读，不支持 DRM |
| SVG | 支持部分图形与文字样式修改、尺寸调整和翻转，保留矢量格式 |
| PNG、JPG/JPEG | 支持添加文字、调整尺寸和翻转；保存后文字合成为图片 |
| 其他图片 | 依赖 Qt 图像插件提供静态只读预览 |

Office 复杂排版和高级对象可能无法完整显示。阅读转换不会写回源文件；损坏、加密或不受支持的文件会提示错误。

### 脑图与流程图

通过“文件”菜单新建，或打开 `.nvdiagram`、`.drawio`、`.xmind`。

支持离线节点与连线编辑、拖动、多页、撤销重做、缩放，以及 SVG/PNG 导出。自有 `.nvdiagram` 格式用于完整保存当前支持的编辑数据；draw.io 和新版 XMind 仅支持部分结构。

复杂样式、附件、特殊结构及旧版 XMind `content.xml` 不保证兼容。发现不支持的信息时会提示并保护原路径，转换副本需确认后另存。

## 工作区与数据保存

文档工作区默认保存在 `%LOCALAPPDATA%\Notepad View\workspace.sqlite3`，并保留上一份有效备份。启动时恢复文件分类、草稿、未保存文本及当前文件等状态，文本显示模式始终回到源码单栏；图片、PDF、Office 和电子书的恢复范围随文件类型不同。

- 工作区缓存独立于原文件，只有手动保存才写入原文档。
- 文档缓存以本地明文保存，不自动上传；不建议在共享电脑上保留敏感草稿。
- 自动缓存通常在停止操作约 0.8 秒后写入，持续编辑时定期尝试缓存；异常断电仍可能丢失最近输入。
- 主题等界面偏好使用 QSettings 保存；文本视图模式不持久化。
- 重复启动会激活已有窗口并转交文件路径。

## 常用快捷键

| 快捷键 | 操作 |
| --- | --- |
| `Ctrl+N` | 新建文档 |
| `Ctrl+O` | 打开文件 |
| `Ctrl+S` | 保存 |
| `Ctrl+Shift+S` | 另存为 |
| `Ctrl+F` | 查找替换／带入选区 |
| `Ctrl+Shift+F` | 格式化文档或选区 |
| `Ctrl+1 / Ctrl+2 / Ctrl+3` | 切换源码／双栏／预览视图 |
| `F1` | 打开使用帮助 |
| `F2` | 重命名文件 |
| `Alt+鼠标拖动` | 矩形列选择 |

## 从源码运行

建议使用 **Windows 10/11 x64、Python 3.12**。核心界面依赖锁定为 PySide6 6.11.1 和 pyside6-scintilla 5.6.3.7，请使用仓库提供的版本。

```powershell
git clone https://github.com/shenxiangxiong/notepadview.git
cd notepadview
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe main.py
```

运行自动化测试：

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q
```

部分测试会创建原生窗口或 Qt WebEngine 子进程，需要可用的桌面环境。

## Windows 打包

在已准备好依赖的项目根目录执行：

```powershell
.\build.ps1
```

也可使用本地生成的 `Windows打包.exe`，它调用同一套构建脚本。该 EXE 属于生成产物，不随源码提交；需要时重新生成：

```powershell
.\.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean --onefile --console --name Windows打包 --distpath . --workpath build\windows-packager --specpath build scripts\windows_packager.py
```

构建前还需准备 `logo` 目录中的品牌源图（推荐 `logo\nvlogo.png`）；当前源码提交未包含该本地素材目录，构建脚本会用它重新生成程序图标。

首次构建会下载固定版本的 LibreOffice、Calibre，校验 SHA256 后提取到构建缓存，不安装到系统。后续构建可复用缓存，也可通过 `NOTEPADVIEW_LIBREOFFICE_DIR`、`NOTEPADVIEW_CALIBRE_DIR` 指定完整运行时目录。运行时再分发要求见 [第三方组件说明](THIRD_PARTY_NOTICES.md)。

构建产物位于 `dist`：

- `NotepadView-1.0.0-Setup.exe`：安装版，内部采用固实 TAR.XZ 无损压缩。
- `NotepadView-1.0.0-Windows-x64-portable.zip`：便携版，使用资源管理器兼容的 Deflate。
- `SHA256SUMS.txt`：发行文件校验值。

压缩保留完整运行时，不减少功能，也不改变安装后的磁盘占用。固实压缩构建耗时较长。一键工具日志保存在 `build\package-latest.log`。

仅对已有发行目录重新组装安装包可执行以下命令；**修改应用源码后应完整重建**：

```powershell
.\.venv\Scripts\python.exe scripts\build.py --setup-only
```

### macOS 构建状态

[macOS 构建脚本](scripts/build_macos.py) 面向 macOS 13+、Apple Silicon 和原生 arm64 Python 3.11–3.14，依赖见 [requirements-macos.txt](requirements-macos.txt)。需在真实 Mac 上执行，Windows 不支持交叉生成。

当前尚未完成真实 Mac 构建及运行验证。脚本仅提供本地 ad-hoc 签名，未实现 Developer ID 签名、公证和 stapling，不能视为已验证的 macOS 正式发行版。

## 项目结构

```text
notepadview/       应用源码、界面和必要资源
packaging_tools/  安装器、卸载及运行时打包逻辑
scripts/          构建、资源生成与开发验证脚本
tests/            自动化测试及测试数据
portal/           静态介绍页面
main.py           应用启动入口
build.ps1         Windows 构建入口
requirements*.txt 运行与开发依赖
```

安装包、虚拟环境、缓存及本地宣传素材不属于源码提交范围。

## 许可与发布说明

第三方依赖及其再分发说明见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。分发时需保留相应许可证、声明，并核查所用运行时版本的许可要求。

历史 PDF 编辑模块仍依赖 PyMuPDF（MuPDF），采用 **AGPL-3.0 或商业许可**；当前 PDF 查看使用 Qt PDF，不代表该依赖的许可义务自动消失。使用或分发前应评估适用要求，必要时取得商业许可。参见 [PyMuPDF 许可说明](https://pymupdf.readthedocs.io/en/latest/about.html#license-and-copyright)。

当前本地 Windows 构建未配置代码签名证书。下载后可核对发布的 SHA256 校验值；正式发布前应确认对应版本的测试结果及依赖许可。
