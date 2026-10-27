# Third-party Components

Notepad View uses the following components. Copyrights remain with their respective authors.

- Python 3.12: Python Software Foundation License.
- PySide6, Shiboken6 and Qt 6: LGPLv3/GPLv3/commercial licensing, depending on component. This distribution uses dynamically linked Qt libraries and does not restrict replacement of those libraries. Qt WebEngine includes Chromium and its third-party notices.
- markdown-it-py, mdurl, mdit-py-plugins: MIT license.
- Pygments: BSD-2-Clause license.
- pyside6-scintilla: MIT license; Scintilla uses the Historical Permission Notice and Disclaimer.
- sqlparse: BSD-3-Clause license.
- python-docx and openpyxl (Word/Excel support in source builds): MIT license.
- lxml: BSD-3-Clause license; includes libxml2/libxslt with their upstream licenses.
- et_xmlfile: MIT license; typing_extensions: Python Software Foundation License.
- PyMuPDF 1.26.7 / MuPDF (PDF editing in source builds): dual AGPL-3.0 or commercial licensing, provided by Artifex. See https://pymupdf.readthedocs.io/en/latest/about.html#license-and-copyright and https://artifex.com/licensing/. No compatibility with this application's distribution terms is asserted. Evaluate AGPL obligations or obtain an appropriate commercial license before redistribution. Existing installers were not rebuilt to include this dependency.
- regex: Apache-2.0 and CNRI-Python licenses, as recorded in the bundled component metadata.
- HTTPX and httpcore: BSD-3-Clause; h11: MIT; certifi: Mozilla Public License 2.0; idna: BSD-3-Clause.
- PyYAML: MIT license.
- cryptography: Apache-2.0 or BSD-3-Clause; cffi: MIT; keyring and jaraco.classes: MIT; pywin32-ctypes: BSD-3-Clause.
- PyInstaller build bootloader: GPL with bootloader exception permitting distribution of bundled applications under other licenses.
- Windows reader runtimes: the build automatically downloads LibreOffice 26.2.6 and Calibre 9.17.0 from their upstream distribution sites, verifies pinned SHA256 digests and extracts the complete runtime trees, including vendor license and notice files. Each downloaded runtime includes `runtime-origin.json` recording its version, installer URL, checksum and corresponding upstream source URL. LibreOffice source: https://download.documentfoundation.org/libreoffice/src/26.2.6/ ; Calibre source: https://download.calibre-ebook.com/9.17.0/calibre-9.17.0.tar.xz . LibreOffice is primarily MPL-2.0; Calibre is GPL-3.0; both include additional third-party components under their respective licenses. These source URLs are references, not a substitute for any applicable corresponding-source distribution obligations. Custom Windows inputs and macOS runtimes remain builder-supplied. Preserve all accompanying licenses and notices and satisfy the obligations of the distributions actually shipped.

Component metadata and license texts are included under `_internal/licenses` on Windows, or `Notepad View.app/Contents/Resources/licenses` on macOS. Qt WebEngine notices are included there when provided by the upstream wheels. No modifications to Qt or Chromium were made. Corresponding upstream versions are recorded in `requirements.txt` and `dependencies-lock.txt`.
