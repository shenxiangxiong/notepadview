# Notepad View

一款基于 Python、PySide6 和 Scintilla 的桌面编辑工具，将文本编辑、Markdown 预览、文件阅读与 API 调试集中在同一个工作区。

支持源码、双栏和预览三种视图，提供文件分类、工作区恢复、查找替换及多主题界面。当前主要面向 **Windows 10/11 x64**；macOS Apple Silicon 构建脚本处于准备阶段，尚未在真实 Mac 上验证。

> 本文介绍当前源码功能。已发布安装包可能与源码进度不同，请以对应版本的发布说明为准。

## 功能概览

| 功能 | 说明 |
| --- | --- |
| 文本与代码编辑 | 多类型语法高亮、行号、矩形列选择、编码转换与独立撤销历史 |
| Markdown | 实时预览、标题目录、源码与预览定位、导出带主题样式的 HTML |
| 查找替换 | 右上角悬浮面板，可调整宽度；支持大小写、全字和正则匹配 |
| 结构化文本 | JSON、JSONL、XML、SQL 格式化，以及 JSON/XML 检查和修复预览 |
| 文件工作区 | 虚拟分类、多文档切换、拖放打开、自动缓存和会话恢复 |
| 文档阅读 | PDF、Word、Excel、其他 Office 格式及电子书阅读 |
| 图片处理 | 部分格式支持文字标注、尺寸调整、翻转与保存 |
| 脑图与流程图 | 离线画布、节点和连线编辑、多页、SVG/PNG 导出 |
| API 工作台 | OpenAPI 导入、请求调试、环境变量、认证、脚本和历史记录 |
| 界面设置 | NV、浅色、深色、护眼主题；侧栏左右切换与宽度调整 |

## 安装与使用

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

支持 Markdown、TXT/日志、JSON/JSONL、YAML、XML、HTML、CSS/SCSS、JavaScript/TypeScript、SQL、Python、PowerShell、INI/TOML、CSV/TSV 等类型。未知后缀及无后缀文件会尝试可靠的文本识别；无法解码的二进制文件不会强制作为文本打开。

- **Markdown**：支持标题、列表、待办、表格、引用、代码高亮、图片及链接。Markdown 中的 HTML 按文本显示，不执行脚本。暂不支持公式和 Mermaid 渲染。
- **HTML**：预览可执行文档内脚本，并可能加载在线资源，请仅预览可信内容。
- **格式化**：JSON、JSONL、XML、SQL 提供类型对应的格式化；优先处理选区，否则处理全文。其他类型暂不提供通用格式化。
- **修复**：JSON/XML 支持有限、保守的语法修复，先展示修改和差异，确认后应用；失败保留原文，不自动保存原文件。
- **脚本运行**：Windows 下可在独立终端运行 Python 和 PowerShell 文件，执行前先保存。打包版运行 Python 脚本仍需可用的项目或系统 Python。
- **编码**：支持 UTF-8、UTF-8 BOM、UTF-16 等编码重读和转换；ANSI 使用本机代码页。转换后手动保存才写入磁盘，不能表示的字符会阻止保存。

文本采用同目录临时文件原子替换保存。HTML 导出不内嵌图片，分享时需要一并提供相关图片资源。

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
| EPUB | 章节阅读、目录、缩放和位置恢复；禁用脚本及远程内容 |
| MOBI、AZW3、FB2 | 通过 Calibre 转换为 EPUB 阅读，不支持 DRM |
| SVG | 支持部分图形与文字样式修改、尺寸调整和翻转，保留矢量格式 |
| PNG、JPG/JPEG | 支持添加文字、调整尺寸和翻转；保存后文字合成为图片 |
| 其他图片 | 依赖 Qt 图像插件提供静态只读预览 |

Office 复杂排版和高级对象可能无法完整显示。阅读转换不会写回源文件；损坏、加密或不受支持的文件会提示错误。

### 脑图与流程图

通过“文件”菜单新建，或打开 `.nvdiagram`、`.drawio`、`.xmind`。

支持离线节点与连线编辑、拖动、多页、撤销重做、缩放，以及 SVG/PNG 导出。自有 `.nvdiagram` 格式用于完整保存当前支持的编辑数据；draw.io 和新版 XMind 仅支持部分结构。

复杂样式、附件、特殊结构及旧版 XMind `content.xml` 不保证兼容。发现不支持的信息时会提示并保护原路径，转换副本需确认后另存。

## API 工作台

从主菜单进入独立 API 请求工作区。

- 导入 Swagger 2.0、OpenAPI 3.0/3.1，支持本地 JSON/YAML、URL 和粘贴内容。
- 管理模块与接口，配置 Query、Headers、Cookies、认证及请求正文。
- 支持 raw、表单、multipart、二进制正文，以及 Basic、Bearer、API Key 认证。
- 提供开发、测试、生产环境与变量，请求前和响应后 JavaScript 脚本在受限子进程执行并设置超时。
- 查看响应状态、耗时、正文、断言及最近 100 条请求历史。
- 请求内容需点击保存才持久化；切换接口会放弃尚未保存的请求内容。

HTTP/HTTPS 请求由本机发送，TLS 校验默认开启。敏感数据使用 AES-GCM 加密并通过系统密钥库保护。暂不支持 Postman Collection 导入/导出，也未实现 OAuth 2.0 授权码 + PKCE 或 Client Credentials 流程。

## 工作区与数据保存

文档工作区默认保存在 `%LOCALAPPDATA%\Notepad View\workspace.sqlite3`，并保留上一份有效备份。启动时恢复文件分类、草稿、未保存文本、当前文件及视图等状态；图片、PDF、Office 和电子书的恢复范围随文件类型不同。

- 工作区缓存独立于原文件，只有手动保存才写入原文档。
- 文档缓存以本地明文保存，不自动上传；不建议在共享电脑上保留敏感草稿。
- 自动缓存通常在停止操作约 0.8 秒后写入，持续编辑时定期尝试缓存；异常断电仍可能丢失最近输入。
- 主题等界面偏好使用 QSettings，API 历史与文档缓存分开存储。
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
