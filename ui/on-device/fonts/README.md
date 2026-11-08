# DaOS Chinese UI font

`NotoSansCJKsc-Regular.otf` and `NotoSansCJKsc-Medium.otf` are unmodified **static**
Simplified Chinese OpenType CFF fonts from the official
[Noto CJK repository](https://github.com/notofonts/noto-cjk),
fixed at commit `f8d157532fbfaeda587e826d4cd5b21a49186f7c`.
Regular (body) is 16,437,364 bytes; Medium (headings) is 16,523,632 bytes. Both
use the included SIL Open Font License 1.1 (`OFL.txt`). `SOURCE.json` records exact
upstream URLs, SHA-256 and Git blob SHA-1 identities for fonts and license.
No Windows-installed font was copied.

Register after constructing QGuiApplication and before loading QML:

```python
from PyQt5.QtGui import QFont, QFontDatabase

for name in ('NotoSansCJKsc-Regular.otf', 'NotoSansCJKsc-Medium.otf'):
    QFontDatabase.addApplicationFont(str(ui_dir / 'fonts' / name))
body = QFont('Noto Sans CJK SC', 10)
body.setWeight(QFont.Normal)  # Qt weight 50: Regular
app.setFont(body)
heading = QFont('Noto Sans CJK SC', 22)
heading.setWeight(QFont.Medium)  # Qt weight 57: Medium
```

The UI uses one sans-serif family with two weights. Medium provides the title
hierarchy while Regular preserves clear labels, controls and text. The heading
face must be selected with weight 57; family name alone does not select Medium.
The application font can also provide Chinese fallback alongside an existing
Latin font; setting this family explicitly makes font choice deterministic.
No global OS font installation or fontconfig cache modification is required.

Qt 5.15.2 desktop validation registers both faces, resolves Regular/Medium by
weights 50/57, confirms CFF tables with no variable-font `fvar` tables, and resolves
Chinese/Latin/digit sample glyphs. The standalone 800×480 title/body pairing proof
(`typography-sans-medium-800x480.png`), input screenshots and validation reports
are in `build/DaOS_Camera_Experience_001/keyboard-qa/`.
This is not a claim of hardware rendering performance on FreeBSD/Raspberry Pi.
