from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QRadioButton,
    QPushButton,
    QVBoxLayout,
)

from .settings import (
    DuplicateOptions,
    DuplicateDetectionSettings,
    DuplicateActionSettings,
    DuplicateScopeSettings,
)


class DuplicateOptionsDialog(QDialog):
    def __init__(self, options: DuplicateOptions, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Duplicate File Options")
        self.resize(520, 520)

        self._options = options

        layout = QVBoxLayout(self)

        # ---------------------------
        # Detection
        # ---------------------------
        detection_group = QGroupBox("Detection")
        detection_layout = QVBoxLayout(detection_group)

        self.det_name_size = QRadioButton("Same filename and file size")
        self.det_binary = QRadioButton("Exact binary match")
        self.det_image_exact = QRadioButton("Same decoded image content (ignore metadata)")
        self.det_image_similar = QRadioButton("Similar image content")

        self.detection_buttons = QButtonGroup(self)
        self.detection_buttons.addButton(self.det_name_size)
        self.detection_buttons.addButton(self.det_binary)
        self.detection_buttons.addButton(self.det_image_exact)
        self.detection_buttons.addButton(self.det_image_similar)

        detection_layout.addWidget(self.det_name_size)
        detection_layout.addWidget(self.det_binary)
        detection_layout.addWidget(self.det_image_exact)
        detection_layout.addWidget(self.det_image_similar)

        threshold_row = QFormLayout()
        self.similarity_combo = QComboBox()
        self.similarity_combo.addItems(["high", "medium", "low"])
        threshold_row.addRow("Similarity threshold", self.similarity_combo)
        detection_layout.addLayout(threshold_row)

        # ---------------------------
        # Action
        # ---------------------------
        action_group = QGroupBox("Action")
        action_layout = QVBoxLayout(action_group)

        self.act_rename = QRadioButton("Keep both and rename duplicates")
        self.act_skip = QRadioButton("Skip incoming file")
        self.act_keep_best = QRadioButton("Keep best version and remove the others")
        self.act_ask = QRadioButton("Ask for each conflict")

        self.action_buttons = QButtonGroup(self)
        self.action_buttons.addButton(self.act_rename)
        self.action_buttons.addButton(self.act_skip)
        self.action_buttons.addButton(self.act_keep_best)
        self.action_buttons.addButton(self.act_ask)

        action_layout.addWidget(self.act_rename)
        action_layout.addWidget(self.act_skip)
        action_layout.addWidget(self.act_keep_best)
        action_layout.addWidget(self.act_ask)

        best_row = QFormLayout()
        self.best_rule_combo = QComboBox()
        self.best_rule_combo.addItems([
            "highest_resolution",
            "largest_file_size",
            "prefer_existing",
            "prefer_incoming",
        ])
        best_row.addRow("Best version rule", self.best_rule_combo)
        action_layout.addLayout(best_row)

        # ---------------------------
        # Scope
        # ---------------------------
        scope_group = QGroupBox("Scope")
        scope_layout = QFormLayout(scope_group)

        self.chk_copy = QCheckBox("Apply to copy operations")
        self.chk_move = QCheckBox("Apply to move operations")
        self.chk_rename = QCheckBox("Apply to rename operations")

        self.file_types_combo = QComboBox()
        self.file_types_combo.addItems(["images_only", "images_and_videos"])

        scope_layout.addRow(self.chk_copy)
        scope_layout.addRow(self.chk_move)
        scope_layout.addRow(self.chk_rename)
        scope_layout.addRow("File types", self.file_types_combo)

        info_label = QLabel(
            "These rules apply only to the selected operation types. "
            "Other operations continue with normal conflict handling."
        )
        info_label.setWordWrap(True)

        buttons = QHBoxLayout()
        ok_btn = QPushButton("OK")
        cancel_btn = QPushButton("Cancel")
        ok_btn.clicked.connect(self.accept)
        cancel_btn.clicked.connect(self.reject)
        buttons.addStretch(1)
        buttons.addWidget(ok_btn)
        buttons.addWidget(cancel_btn)

        layout.addWidget(detection_group)
        layout.addWidget(action_group)
        layout.addWidget(scope_group)
        layout.addWidget(info_label)
        layout.addLayout(buttons)

        self._load_from_options()
        self._update_enabled_state()

        self.det_name_size.toggled.connect(self._update_enabled_state)
        self.det_binary.toggled.connect(self._update_enabled_state)
        self.det_image_exact.toggled.connect(self._update_enabled_state)
        self.det_image_similar.toggled.connect(self._update_enabled_state)

        self.act_rename.toggled.connect(self._update_enabled_state)
        self.act_skip.toggled.connect(self._update_enabled_state)
        self.act_keep_best.toggled.connect(self._update_enabled_state)
        self.act_ask.toggled.connect(self._update_enabled_state)

    def _load_from_options(self) -> None:
        method = self._options.detection.method
        if method == "name_size":
            self.det_name_size.setChecked(True)
        elif method == "binary_exact":
            self.det_binary.setChecked(True)
        elif method == "image_exact":
            self.det_image_exact.setChecked(True)
        else:
            self.det_image_similar.setChecked(True)

        self.similarity_combo.setCurrentText(self._options.detection.similarity_threshold)

        action = self._options.action.action
        if action == "rename":
            self.act_rename.setChecked(True)
        elif action == "skip":
            self.act_skip.setChecked(True)
        elif action == "keep_best":
            self.act_keep_best.setChecked(True)
        else:
            self.act_ask.setChecked(True)

        self.best_rule_combo.setCurrentText(self._options.action.best_version_rule)

        self.chk_copy.setChecked(self._options.scope.apply_on_copy)
        self.chk_move.setChecked(self._options.scope.apply_on_move)
        self.chk_rename.setChecked(self._options.scope.apply_on_rename)
        self.file_types_combo.setCurrentText(self._options.scope.file_types)

    def _update_enabled_state(self) -> None:
        is_similar = self.det_image_similar.isChecked()
        self.similarity_combo.setEnabled(is_similar)

        keep_best = self.act_keep_best.isChecked()
        self.best_rule_combo.setEnabled(keep_best)

        # Güvenlik: gevşek detection ile agresif silme istemiyorsan burada uyarı/pasifleştirme eklenebilir
        # Şimdilik sadece bırakıyoruz.

    def build_options(self) -> DuplicateOptions:
        if self.det_name_size.isChecked():
            method = "name_size"
        elif self.det_binary.isChecked():
            method = "binary_exact"
        elif self.det_image_exact.isChecked():
            method = "image_exact"
        else:
            method = "image_similar"

        if self.act_rename.isChecked():
            action = "rename"
        elif self.act_skip.isChecked():
            action = "skip"
        elif self.act_keep_best.isChecked():
            action = "keep_best"
        else:
            action = "ask"

        return DuplicateOptions(
            detection=DuplicateDetectionSettings(
                method=method,
                similarity_threshold=self.similarity_combo.currentText(),
            ),
            action=DuplicateActionSettings(
                action=action,
                best_version_rule=self.best_rule_combo.currentText(),
            ),
            scope=DuplicateScopeSettings(
                apply_on_copy=self.chk_copy.isChecked(),
                apply_on_move=self.chk_move.isChecked(),
                apply_on_rename=self.chk_rename.isChecked(),
                file_types=self.file_types_combo.currentText(),
            ),
        )