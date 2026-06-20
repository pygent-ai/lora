from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontDatabase, QFontInfo, QPalette
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDateEdit,
    QDateTimeEdit,
    QDial,
    QDoubleSpinBox,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLCDNumber,
    QLineEdit,
    QListWidget,
    QMainWindow,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QScrollBar,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QSplitter,
    QStyle,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextEdit,
    QTimeEdit,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from gui.icons import icon
from gui.resource_catalog import ResourceItem, build_resource_catalog, resource_spec_as_markdown


class ResourcesWindow(QMainWindow):
    def __init__(self, catalog: tuple[ResourceItem, ...] | None = None, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Lora Resources")
        self.setObjectName("ResourcesWindow")
        self._catalog = catalog or build_resource_catalog()
        self._item_by_id = {item.resource_id: item for item in self._catalog}
        self._active_category = "All"
        self._selected_resource_id: str | None = None
        self._visible_items: tuple[ResourceItem, ...] = self._catalog
        self._card_by_id: dict[str, _ResourceCard] = {}
        self._color_lab_hex = "#147efb"

        shell = QWidget()
        shell.setObjectName("ResourcesShell")
        shell_layout = QVBoxLayout(shell)
        shell_layout.setContentsMargins(16, 16, 16, 16)
        shell_layout.setSpacing(12)

        header = _resource_header()
        shell_layout.addWidget(header)

        splitter = QSplitter(Qt.Horizontal)
        splitter.setObjectName("ResourcesSplitter")
        splitter.addWidget(self._build_category_pane())
        splitter.addWidget(self._build_gallery_pane())
        splitter.addWidget(self._build_spec_pane())
        splitter.setSizes([220, 680, 360])
        shell_layout.addWidget(splitter, 1)

        self.setCentralWidget(shell)
        self._populate_categories()
        self._apply_filter()
        if self._catalog:
            self.select_resource(self._catalog[0].resource_id)

    def catalog_items(self) -> tuple[ResourceItem, ...]:
        return self._catalog

    def selected_resource_id(self) -> str | None:
        return self._selected_resource_id

    def spec_text(self) -> str:
        return self.spec.toPlainText()

    def visible_resource_names(self) -> list[str]:
        return [item.name for item in self._visible_items]

    def set_search_text(self, text: str) -> None:
        self.search.setText(text)
        self._apply_filter()

    def select_resource(self, resource_id: str) -> None:
        item = self._item_by_id.get(resource_id)
        if item is None:
            return
        self._selected_resource_id = resource_id
        self.spec_title.setText(item.name)
        self.spec_meta.setText(f"{item.category}  |  {item.source}")
        self.spec.setPlainText(resource_spec_as_markdown(item))
        if item.category == "Colors":
            self.set_color_lab_hex(item.value)
        for card_id, card in self._card_by_id.items():
            card.setChecked(card_id == resource_id)

    def copy_selected_spec(self) -> None:
        QApplication.clipboard().setText(self.spec_text())

    def color_lab_hex(self) -> str:
        return self._color_lab_hex

    def set_color_lab_hex(self, value: str) -> None:
        normalized = _normalize_hex_color(value)
        if normalized is None:
            return
        self._color_lab_hex = normalized
        self.color_lab_input.blockSignals(True)
        self.color_lab_input.setText(normalized)
        self.color_lab_input.blockSignals(False)
        self._set_color_lab_input_invalid(False)
        self._refresh_color_lab()

    def copy_color_lab_hex(self) -> None:
        QApplication.clipboard().setText(self._color_lab_hex)

    def copy_color_lab_rgb(self) -> None:
        QApplication.clipboard().setText(_rgb_text(self._color_lab_hex))

    def _build_category_pane(self) -> QWidget:
        pane = QFrame()
        pane.setObjectName("ResourceCategoryPane")
        pane.setAttribute(Qt.WA_StyledBackground, True)
        layout = QVBoxLayout(pane)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        label = QLabel("Resources")
        label.setObjectName("ResourcePaneTitle")
        self.categories = QTreeWidget()
        self.categories.setObjectName("ResourceCategoryTree")
        self.categories.setHeaderHidden(True)
        self.categories.setRootIsDecorated(False)
        self.categories.itemSelectionChanged.connect(self._on_category_changed)

        layout.addWidget(label)
        layout.addWidget(self.categories, 1)
        return pane

    def _build_gallery_pane(self) -> QWidget:
        pane = QFrame()
        pane.setObjectName("ResourceGalleryPane")
        pane.setAttribute(Qt.WA_StyledBackground, True)
        layout = QVBoxLayout(pane)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(12)

        self.search = QLineEdit()
        self.search.setObjectName("ResourceSearchInput")
        self.search.setPlaceholderText("Search fonts, components, colors, styles, icons")
        self.search.textChanged.connect(self._apply_filter)

        self.scroll = QScrollArea()
        self.scroll.setObjectName("ResourcePreviewScroll")
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.card_host = QWidget()
        self.card_host.setObjectName("ResourceCardHost")
        self.grid = QGridLayout(self.card_host)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setHorizontalSpacing(10)
        self.grid.setVerticalSpacing(10)
        self.scroll.setWidget(self.card_host)

        layout.addWidget(self.search)
        layout.addWidget(self.scroll, 1)
        return pane

    def _build_spec_pane(self) -> QWidget:
        pane = QFrame()
        pane.setObjectName("ResourceSpecPane")
        pane.setAttribute(Qt.WA_StyledBackground, True)
        layout = QVBoxLayout(pane)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        self.spec_title = QLabel("Select a resource")
        self.spec_title.setObjectName("ResourceSpecTitle")
        self.spec_title.setWordWrap(True)
        self.spec_meta = QLabel("")
        self.spec_meta.setObjectName("ResourceSpecMeta")
        self.spec_meta.setWordWrap(True)
        self.spec = QPlainTextEdit()
        self.spec.setObjectName("ResourceSpecText")
        self.spec.setReadOnly(True)
        self.spec.setLineWrapMode(QPlainTextEdit.WidgetWidth)
        self.copy_button = QPushButton("Copy Spec")
        self.copy_button.setObjectName("ResourceCopyButton")
        self.copy_button.clicked.connect(self.copy_selected_spec)

        layout.addWidget(self.spec_title)
        layout.addWidget(self.spec_meta)
        layout.addWidget(self.spec, 1)
        layout.addWidget(self.copy_button)
        layout.addWidget(self._build_color_lab())
        return pane

    def _build_color_lab(self) -> QWidget:
        lab = QFrame()
        lab.setObjectName("ResourceColorLab")
        lab.setAttribute(Qt.WA_StyledBackground, True)
        layout = QVBoxLayout(lab)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        title = QLabel("Color Lab")
        title.setObjectName("ResourceColorLabTitle")

        self.color_lab_swatch = QLabel(self._color_lab_hex)
        self.color_lab_swatch.setObjectName("ResourceColorLabSwatch")
        self.color_lab_swatch.setAlignment(Qt.AlignCenter)
        self.color_lab_swatch.setMinimumHeight(42)

        self.color_lab_input = QLineEdit(self._color_lab_hex)
        self.color_lab_input.setObjectName("ResourceColorLabInput")
        self.color_lab_input.setPlaceholderText("#147efb")
        self.color_lab_input.textChanged.connect(self._on_color_lab_input_changed)

        button_row = QHBoxLayout()
        button_row.setContentsMargins(0, 0, 0, 0)
        button_row.setSpacing(6)
        self.pick_color_button = QPushButton("Pick Color")
        self.pick_color_button.setObjectName("ResourcePickColorButton")
        self.pick_color_button.clicked.connect(self._pick_color)
        self.copy_hex_button = QPushButton("Copy HEX")
        self.copy_hex_button.setObjectName("ResourceCopyHexButton")
        self.copy_hex_button.clicked.connect(self.copy_color_lab_hex)
        self.copy_rgb_button = QPushButton("Copy RGB")
        self.copy_rgb_button.setObjectName("ResourceCopyRgbButton")
        self.copy_rgb_button.clicked.connect(self.copy_color_lab_rgb)
        button_row.addWidget(self.pick_color_button)
        button_row.addWidget(self.copy_hex_button)
        button_row.addWidget(self.copy_rgb_button)

        layout.addWidget(title)
        layout.addWidget(self.color_lab_swatch)
        layout.addWidget(self.color_lab_input)
        layout.addLayout(button_row)
        self._refresh_color_lab()
        return lab

    def _on_color_lab_input_changed(self, text: str) -> None:
        normalized = _normalize_hex_color(text)
        if normalized is None:
            self._set_color_lab_input_invalid(True)
            return
        self._color_lab_hex = normalized
        self._set_color_lab_input_invalid(False)
        self._refresh_color_lab()

    def _pick_color(self) -> None:
        selected = QColorDialog.getColor(QColor(self._color_lab_hex), self, "Pick UI Color")
        if selected.isValid():
            self.set_color_lab_hex(selected.name())

    def _refresh_color_lab(self) -> None:
        if not hasattr(self, "color_lab_swatch"):
            return
        contrast = _contrast_text(self._color_lab_hex)
        self.color_lab_swatch.setText(f"{self._color_lab_hex}  |  {_rgb_text(self._color_lab_hex)}")
        self.color_lab_swatch.setProperty("preview_value", self._color_lab_hex)
        self.color_lab_swatch.setStyleSheet(
            f"background: {self._color_lab_hex}; border-radius: 9px; color: {contrast};"
        )

    def _set_color_lab_input_invalid(self, invalid: bool) -> None:
        self.color_lab_input.setProperty("invalid", invalid)
        self.color_lab_input.style().unpolish(self.color_lab_input)
        self.color_lab_input.style().polish(self.color_lab_input)

    def _populate_categories(self) -> None:
        self.categories.clear()
        for category in ("All", *sorted({item.category for item in self._catalog})):
            tree_item = QTreeWidgetItem([category])
            tree_item.setData(0, Qt.UserRole, category)
            self.categories.addTopLevelItem(tree_item)
            if category == "All":
                self.categories.setCurrentItem(tree_item)

    def _on_category_changed(self) -> None:
        selected = self.categories.currentItem()
        self._active_category = str(selected.data(0, Qt.UserRole)) if selected is not None else "All"
        self._apply_filter()

    def _apply_filter(self) -> None:
        query = self.search.text().strip().lower() if hasattr(self, "search") else ""
        visible = []
        for item in self._catalog:
            if self._active_category != "All" and item.category != self._active_category:
                continue
            haystack = " ".join((item.name, item.category, item.source, item.summary, item.value)).lower()
            if query and query not in haystack:
                continue
            visible.append(item)
        self._visible_items = tuple(visible)
        self._render_cards()

    def _render_cards(self) -> None:
        while self.grid.count():
            layout_item = self.grid.takeAt(0)
            widget = layout_item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        self._card_by_id.clear()

        for index, item in enumerate(self._visible_items):
            card = _resource_card(item)
            card.clicked.connect(lambda _checked=False, resource_id=item.resource_id: self.select_resource(resource_id))
            row, column = divmod(index, 3)
            self.grid.addWidget(card, row, column)
            self._card_by_id[item.resource_id] = card
        for column in range(3):
            self.grid.setColumnStretch(column, 1)
        if self._selected_resource_id in self._card_by_id:
            self._card_by_id[self._selected_resource_id].setChecked(True)


def _resource_header() -> QWidget:
    header = QFrame()
    header.setObjectName("ResourceHeader")
    header.setAttribute(Qt.WA_StyledBackground, True)
    layout = QHBoxLayout(header)
    layout.setContentsMargins(14, 12, 14, 12)
    layout.setSpacing(8)
    title = QLabel("Lora Resources")
    title.setObjectName("ResourceHeaderTitle")
    subtitle = QLabel("Fonts, components, styles, colors, and icons from the current PySide6 route.")
    subtitle.setObjectName("ResourceHeaderSubtitle")
    subtitle.setWordWrap(True)
    text = QVBoxLayout()
    text.setContentsMargins(0, 0, 0, 0)
    text.setSpacing(2)
    text.addWidget(title)
    text.addWidget(subtitle)
    layout.addLayout(text, 1)
    return header


def _resource_card(item: ResourceItem) -> "_ResourceCard":
    return _ResourceCard(item)


class _ResourceCard(QFrame):
    clicked = Signal()

    def __init__(self, item: ResourceItem):
        super().__init__()
        self._checked = False
        self.setObjectName("ResourceCard")
        self.setCursor(Qt.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setMinimumHeight(172 if item.category in {"Fonts", "Components", "Layouts"} else 132)
        self.setToolTip(item.summary)
        self.setProperty("resource_id", item.resource_id)
        self.setProperty("category", item.category)
        self.setProperty("checked", False)
        self.setAttribute(Qt.WA_StyledBackground, True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)
        layout.addWidget(_preview_widget(item))

        name = QLabel(item.name)
        name.setObjectName("ResourceCardName")
        name.setWordWrap(True)
        category = QLabel(item.category)
        category.setObjectName("ResourceCardCategory")
        value = QLabel(item.value if item.value and len(item.value) <= 48 else item.source)
        value.setObjectName("ResourceCardValue")
        value.setWordWrap(True)
        layout.addWidget(name)
        layout.addWidget(category)
        layout.addWidget(value)

    def setChecked(self, checked: bool) -> None:  # noqa: N802 - mirrors Qt button API used by window code.
        self._checked = checked
        self.setProperty("checked", checked)
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()

    def isChecked(self) -> bool:  # noqa: N802 - mirrors Qt button API.
        return self._checked

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(event)


def _preview_widget(item: ResourceItem) -> QWidget:
    if item.category == "Colors":
        return _color_preview(item)
    if item.category == "Fonts":
        return _font_preview(item)
    if item.category == "Components":
        return _component_preview(item)
    if item.category == "Icons":
        return _icon_preview(item)
    if item.category == "Standard Icons":
        return _standard_icon_preview(item)
    if item.category == "Palette Roles":
        return _palette_preview(item)
    if item.category == "Layouts":
        return _layout_preview(item)
    if item.category == "Qt Modules":
        return _module_preview(item)
    return _style_preview(item)


def _color_preview(item: ResourceItem) -> QLabel:
    swatch = QLabel(item.value)
    swatch.setObjectName("ResourceColorSwatch")
    swatch.setProperty("preview_value", item.value)
    swatch.setAlignment(Qt.AlignCenter)
    swatch.setMinimumHeight(38)
    swatch.setStyleSheet(f"background: {item.value}; border-radius: 9px; color: {_contrast_text(item.value)};")
    return swatch


def _font_preview(item: ResourceItem) -> QWidget:
    _register_font_file_for_preview(item)
    preview = QFrame()
    preview.setObjectName("ResourceFontPreview")
    preview.setAttribute(Qt.WA_StyledBackground, True)
    layout = QVBoxLayout(preview)
    layout.setContentsMargins(8, 6, 8, 6)
    layout.setSpacing(3)

    headline = QLabel("Ag")
    headline.setObjectName("ResourceFontHeadline")
    headline_font = _font_for_item(item, pixel_size=32, weight=QFont.Weight.Bold)
    headline.setFont(headline_font)
    headline.setStyleSheet(_font_preview_stylesheet(item.value))

    sample = QLabel("Aa 你好 123")
    sample.setObjectName("ResourceFontSample")
    sample.setFont(_font_for_item(item, pixel_size=18, weight=QFont.Weight.DemiBold))
    sample.setStyleSheet(_font_preview_stylesheet(item.value))

    ruler = QLabel("111111  WWWWWW  iiiiii")
    ruler.setObjectName("ResourceFontRuler")
    ruler.setFont(_font_for_item(item, pixel_size=13, weight=QFont.Weight.Normal))
    ruler.setStyleSheet(_font_preview_stylesheet(item.value))

    resolved = QLabel(f"Resolved: {QFontInfo(sample.font()).family()}")
    resolved.setObjectName("ResourceFontResolved")

    layout.addWidget(headline)
    layout.addWidget(sample)
    layout.addWidget(ruler)
    layout.addWidget(resolved)
    return preview


def _font_for_item(item: ResourceItem, *, pixel_size: int, weight: QFont.Weight) -> QFont:
    font = QFont()
    font.setFamilies([item.value])
    font.setPixelSize(pixel_size)
    font.setWeight(weight)
    if "mono" in item.value.lower() or item.value.lower() in {"consolas", "courier new"}:
        font.setFixedPitch(True)
        font.setStyleHint(QFont.StyleHint.TypeWriter)
    return font


def _register_font_file_for_preview(item: ResourceItem) -> None:
    source = Path(item.source)
    if source.suffix.lower() in {".ttf", ".ttc", ".otf"} and source.exists():
        QFontDatabase.addApplicationFont(str(source))


def _font_preview_stylesheet(family: str) -> str:
    escaped = family.replace('"', '\\"')
    return f'font-family: "{escaped}";'


def _component_preview(item: ResourceItem) -> QWidget:
    if item.name == "QPushButton":
        button = QPushButton("Button")
        button.setObjectName("ResourceComponentPreviewButton")
        button.setEnabled(False)
        return button
    if item.name == "QToolButton":
        button = QToolButton()
        button.setObjectName("ResourceComponentPreviewToolButton")
        button.setText("Tool")
        button.setEnabled(False)
        return button
    if item.name == "QCheckBox":
        checkbox = QCheckBox("Check")
        checkbox.setObjectName("ResourceComponentPreviewCheckBox")
        checkbox.setChecked(True)
        checkbox.setEnabled(False)
        return checkbox
    if item.name == "QRadioButton":
        radio = QRadioButton("Radio")
        radio.setObjectName("ResourceComponentPreviewRadioButton")
        radio.setChecked(True)
        radio.setEnabled(False)
        return radio
    if item.name == "QComboBox":
        combo = QComboBox()
        combo.setObjectName("ResourceComponentPreviewComboBox")
        combo.addItems(["Option", "Alt"])
        combo.setEnabled(False)
        return combo
    if item.name == "QLineEdit":
        line = QLineEdit("Input")
        line.setObjectName("ResourceComponentPreviewInput")
        line.setEnabled(False)
        return line
    if item.name in {"QSpinBox", "QDoubleSpinBox"}:
        spin = QDoubleSpinBox() if item.name == "QDoubleSpinBox" else QSpinBox()
        spin.setObjectName("ResourceComponentPreviewSpinBox")
        spin.setValue(42)
        spin.setEnabled(False)
        return spin
    if item.name == "QSlider":
        slider = QSlider(Qt.Horizontal)
        slider.setObjectName("ResourceComponentPreviewSlider")
        slider.setValue(64)
        slider.setEnabled(False)
        return slider
    if item.name == "QProgressBar":
        progress = QProgressBar()
        progress.setObjectName("ResourceComponentPreviewProgressBar")
        progress.setValue(68)
        progress.setEnabled(False)
        return progress
    if item.name == "QScrollBar":
        scroll = QScrollBar(Qt.Horizontal)
        scroll.setObjectName("ResourceComponentPreviewScrollBar")
        scroll.setValue(45)
        scroll.setEnabled(False)
        return scroll
    if item.name == "QDial":
        dial = QDial()
        dial.setObjectName("ResourceComponentPreviewDial")
        dial.setValue(35)
        dial.setEnabled(False)
        return dial
    if item.name == "QLCDNumber":
        lcd = QLCDNumber()
        lcd.setObjectName("ResourceComponentPreviewLcd")
        lcd.display(128)
        return lcd
    if item.name == "QDateEdit":
        editor = QDateEdit()
        editor.setObjectName("ResourceComponentPreviewDateEdit")
        editor.setEnabled(False)
        return editor
    if item.name == "QTimeEdit":
        editor = QTimeEdit()
        editor.setObjectName("ResourceComponentPreviewTimeEdit")
        editor.setEnabled(False)
        return editor
    if item.name == "QDateTimeEdit":
        editor = QDateTimeEdit()
        editor.setObjectName("ResourceComponentPreviewDateTimeEdit")
        editor.setEnabled(False)
        return editor
    if item.name == "QFontComboBox":
        combo = QComboBox()
        combo.setObjectName("ResourceComponentPreviewFontComboBox")
        combo.addItems(["Microsoft YaHei UI", "Consolas"])
        combo.setEnabled(False)
        return combo
    if item.name == "QListWidget":
        widget = QListWidget()
        widget.setObjectName("ResourceComponentPreviewListWidget")
        widget.addItems(["Item A", "Item B"])
        widget.setEnabled(False)
        return widget
    if item.name == "QTreeWidget":
        widget = QTreeWidget()
        widget.setObjectName("ResourceComponentPreviewTreeWidget")
        widget.setHeaderHidden(True)
        root = QTreeWidgetItem(["Parent"])
        root.addChild(QTreeWidgetItem(["Child"]))
        widget.addTopLevelItem(root)
        root.setExpanded(True)
        widget.setEnabled(False)
        return widget
    if item.name == "QTableWidget":
        widget = QTableWidget(2, 2)
        widget.setObjectName("ResourceComponentPreviewTableWidget")
        widget.setHorizontalHeaderLabels(["A", "B"])
        widget.setItem(0, 0, QTableWidgetItem("1"))
        widget.setItem(0, 1, QTableWidgetItem("2"))
        widget.setEnabled(False)
        return widget
    if item.name == "QTabWidget":
        tabs = QTabWidget()
        tabs.setObjectName("ResourceComponentPreviewTabWidget")
        tabs.addTab(QWidget(), "One")
        tabs.addTab(QWidget(), "Two")
        tabs.setEnabled(False)
        return tabs
    if item.name == "QGroupBox":
        group = QGroupBox("Group")
        group.setObjectName("ResourceComponentPreviewGroupBox")
        layout = QVBoxLayout(group)
        layout.addWidget(QLabel("Grouped content"))
        group.setEnabled(False)
        return group
    if item.name == "QPlainTextEdit":
        edit = QPlainTextEdit("Plain text")
        edit.setObjectName("ResourceComponentPreviewPlainText")
        edit.setEnabled(False)
        return edit
    if item.name == "QTextEdit":
        edit = QTextEdit("Rich text")
        edit.setObjectName("ResourceComponentPreviewTextEdit")
        edit.setEnabled(False)
        return edit
    label = QLabel(item.selector or item.name)
    label.setObjectName("ResourceComponentPreviewLabel")
    label.setAlignment(Qt.AlignCenter)
    label.setMinimumHeight(34)
    return label


def _icon_preview(item: ResourceItem) -> QLabel:
    preview = QLabel()
    preview.setObjectName("ResourceIconPreview")
    preview.setAlignment(Qt.AlignCenter)
    preview.setMinimumHeight(38)
    preview.setPixmap(icon(item.value).pixmap(28, 28))
    return preview


def _standard_icon_preview(item: ResourceItem) -> QLabel:
    preview = QLabel()
    preview.setObjectName("ResourceStandardIconPreview")
    preview.setAlignment(Qt.AlignCenter)
    preview.setMinimumHeight(38)
    app = QApplication.instance()
    style = app.style() if app is not None else None
    if style is not None:
        pixmap = style.standardIcon(getattr(QStyle.StandardPixmap, item.value)).pixmap(30, 30)
        preview.setPixmap(pixmap)
    return preview


def _palette_preview(item: ResourceItem) -> QLabel:
    app = QApplication.instance()
    color = app.palette().color(getattr(QPalette.ColorRole, item.value)).name() if app is not None else "#808080"
    swatch = QLabel(color)
    swatch.setObjectName("ResourcePaletteSwatch")
    swatch.setProperty("palette_role", item.value)
    swatch.setAlignment(Qt.AlignCenter)
    swatch.setMinimumHeight(38)
    swatch.setStyleSheet(f"background: {color}; border-radius: 9px; color: {_contrast_text(color)};")
    return swatch


def _layout_preview(item: ResourceItem) -> QLabel:
    diagrams = {
        "QHBoxLayout": "[ A ][ B ][ C ]",
        "QVBoxLayout": "[ A ]\n[ B ]\n[ C ]",
        "QGridLayout": "[ A ][ B ]\n[ C ][ D ]",
        "QFormLayout": "Label: [ value ]\nLabel: [ value ]",
        "QStackedLayout": "[ page 1 ] behind [ page 2 ]",
        "QSplitter": "[ pane ] | [ pane ]",
        "QScrollArea": "[ viewport ]\ncontent continues...",
    }
    preview = QLabel(diagrams.get(item.name, item.name))
    preview.setObjectName("ResourceLayoutPreview")
    preview.setProperty("layout_name", item.name)
    preview.setAlignment(Qt.AlignCenter)
    preview.setMinimumHeight(58)
    return preview


def _module_preview(item: ResourceItem) -> QLabel:
    preview = QLabel(f"{item.name}\n{item.value}")
    preview.setObjectName("ResourceModulePreview")
    preview.setAlignment(Qt.AlignCenter)
    preview.setMinimumHeight(38)
    return preview


def _style_preview(item: ResourceItem) -> QWidget:
    group = _property_value(item, "group")
    if group == "typography":
        return _typography_style_preview(item)
    if group == "spacing":
        return _spacing_style_preview(item)
    if group == "radius":
        return _radius_style_preview(item)
    if item.resource_id == "style:accent-action":
        button = QPushButton("Primary")
        button.setObjectName("ResourceAccentActionPreview")
        button.setEnabled(False)
        return button
    if item.resource_id == "style:glass-pane":
        return _named_style_preview("ResourceGlassPanePreview", "Glass pane")
    if item.resource_id == "style:segmented-control":
        return _segmented_style_preview()
    preview = QFrame()
    preview.setObjectName("ResourceStylePreview")
    preview.setAttribute(Qt.WA_StyledBackground, True)
    preview.setMinimumHeight(38)
    layout = QHBoxLayout(preview)
    layout.setContentsMargins(8, 6, 8, 6)
    layout.setSpacing(6)
    chip = QLabel(item.value or item.selector or item.name)
    chip.setObjectName("ResourceStylePreviewChip")
    chip.setAlignment(Qt.AlignCenter)
    layout.addWidget(chip)
    return preview


def _typography_style_preview(item: ResourceItem) -> QLabel:
    size = _int_value(item.value, fallback=13)
    preview = QLabel("Type Aa 123")
    preview.setObjectName("ResourceTypographyPreview")
    preview.setProperty("style_group", "typography")
    font = QFont(preview.font())
    font.setPixelSize(size)
    font.setWeight(QFont.Weight.Bold if "title" in item.name else QFont.Weight.DemiBold)
    preview.setFont(font)
    preview.setMinimumHeight(max(38, size + 16))
    preview.setAlignment(Qt.AlignCenter)
    return preview


def _spacing_style_preview(item: ResourceItem) -> QLabel:
    size = _int_value(item.value, fallback=8)
    preview = QLabel(f"{size}px")
    preview.setObjectName("ResourceSpacingPreview")
    preview.setProperty("style_group", "spacing")
    preview.setProperty("preview_value", size)
    preview.setAlignment(Qt.AlignCenter)
    preview.setStyleSheet(
        f"min-width: {size}px; max-width: {max(size, 10)}px; "
        "background: #147efb; border-radius: 4px; color: white; padding: 6px;"
    )
    return preview


def _radius_style_preview(item: ResourceItem) -> QLabel:
    radius = _int_value(item.value, fallback=8)
    preview = QLabel(f"{radius}px")
    preview.setObjectName("ResourceRadiusPreview")
    preview.setProperty("style_group", "radius")
    preview.setAlignment(Qt.AlignCenter)
    preview.setMinimumHeight(42)
    preview.setStyleSheet(
        f"border-radius: {radius}px; border: 2px solid #147efb; "
        "background: rgba(20,126,251,0.14); color: #147efb; padding: 8px;"
    )
    return preview


def _named_style_preview(object_name: str, text: str) -> QLabel:
    preview = QLabel(text)
    preview.setObjectName(object_name)
    preview.setAlignment(Qt.AlignCenter)
    preview.setMinimumHeight(42)
    return preview


def _segmented_style_preview() -> QWidget:
    preview = QFrame()
    preview.setObjectName("ResourceSegmentedPreview")
    preview.setAttribute(Qt.WA_StyledBackground, True)
    layout = QHBoxLayout(preview)
    layout.setContentsMargins(4, 4, 4, 4)
    layout.setSpacing(0)
    for index, text in enumerate(("Day", "Night")):
        label = QLabel(text)
        label.setObjectName("ResourceSegmentedPreviewOption")
        label.setProperty("selected", index == 0)
        label.setAlignment(Qt.AlignCenter)
        layout.addWidget(label)
    return preview


def _property_value(item: ResourceItem, key: str) -> str:
    for property_key, value in item.properties:
        if property_key == key:
            return value
    return ""


def _int_value(value: str, *, fallback: int) -> int:
    try:
        return int(float(value))
    except ValueError:
        return fallback


def _normalize_hex_color(value: str) -> str | None:
    color = value.strip().lower()
    if color.startswith("#"):
        color = color[1:]
    if len(color) == 3 and all(character in "0123456789abcdef" for character in color):
        color = "".join(character * 2 for character in color)
    if len(color) != 6 or any(character not in "0123456789abcdef" for character in color):
        return None
    return f"#{color}"


def _rgb_text(value: str) -> str:
    normalized = _normalize_hex_color(value) or "#000000"
    red = int(normalized[1:3], 16)
    green = int(normalized[3:5], 16)
    blue = int(normalized[5:7], 16)
    return f"rgb({red}, {green}, {blue})"


def _contrast_text(value: str) -> str:
    if not value.startswith("#") or len(value) < 7:
        return "#ffffff"
    try:
        red = int(value[1:3], 16)
        green = int(value[3:5], 16)
        blue = int(value[5:7], 16)
    except ValueError:
        return "#ffffff"
    luminance = (0.299 * red) + (0.587 * green) + (0.114 * blue)
    return "#1f2329" if luminance > 160 else "#ffffff"
