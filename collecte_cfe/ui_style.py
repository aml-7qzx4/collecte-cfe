"""Feuille de style de l’interface : cobalt sur gris clair, titres Bricolage, texte IBM Plex."""

# One colour per dossier situation: what the collaborator has to tell the client.
SITUATION_COLORS = {
    'PAY': ('#FFF0DC', '#A85600'),
    'DEBIT': ('#DFF1F6', '#1A6A80'),
    'NONE': ('#EBEEF4', '#59627A'),
    'REVIEW': ('#FCE5EB', '#B01F48'),
    'ABSENT': ('#F0F2F6', '#767E96'),
    'RETRY': ('#FCE5EB', '#B01F48'),
    'WAIT': ('#F0F2F6', '#767E96'),
    'MANUAL': ('#FDEFD2', '#8A5300'),
    'CHECKED': ('#E3F4EA', '#23704A'),
    'ALL': ('#FFFFFF', '#131A2C'),
}

STYLE = """
QWidget {font-family:"@BODYFONT@"; font-size:13px; color:#131A2C;}
QPlainTextEdit, QTextEdit {background:#FFFFFF; color:#131A2C; border:1px solid #E1E5EE; border-radius:10px; padding:6px 8px; selection-background-color:#E8EBFD; selection-color:#131A2C;}
QPlainTextEdit:focus, QTextEdit:focus {border-color:#2F45D8;}
QToolTip {background:#131A2C; color:#FFFFFF; border:0; padding:6px 8px;}
QWidget#workspace, QWidget#page, QScrollArea#pageScroll {background:#F2F4F8;}
QMessageBox, QInputDialog {background:#FFFFFF; color:#131A2C;}
QMessageBox QLabel, QInputDialog QLabel {background:transparent; color:#131A2C; min-width:280px;}
QMessageBox QPushButton, QInputDialog QPushButton {min-width:88px; background:#2F45D8; color:#FFFFFF; border:1px solid #2F45D8;}
QMessageBox QPushButton:hover, QInputDialog QPushButton:hover {background:#2537B8; border-color:#2537B8;}
QFrame#rail {background:#E9ECF3; border:0; border-right:1px solid #E1E5EE;}
QWidget#headerPanel, QWidget#headerActions {background:transparent;}
QLabel#brand {background:transparent; border:0;}
QLabel#brandName {font-family:"@TITLEFONT@"; color:#131A2C; font-size:15px; font-weight:700;}
QPushButton#railSection {background:transparent; color:#59627A; border:0; text-align:left; padding:6px 10px; font-size:11px; font-weight:600;}
QPushButton#railSection:hover {color:#2F45D8;}
QPushButton#nav {background:transparent; color:#3A4258; border:1px solid transparent; text-align:left; padding:8px 10px; border-radius:8px; font-weight:500;}
QPushButton#nav:hover {background:rgba(255,255,255,0.6);}
QPushButton#nav:checked {background:#FFFFFF; color:#131A2C; font-weight:600; border-color:#E1E5EE;}
QPushButton#campaignNavItem {background:transparent; border:1px solid transparent; border-radius:8px; padding:0; min-height:36px; text-align:left;}
QPushButton#campaignNavItem:hover {background:rgba(255,255,255,0.6);}
QPushButton#campaignNavItem:checked {background:#FFFFFF; border-color:#E1E5EE;}
QLabel#campaignNavName {background:transparent; color:#3A4258;}
QLabel#campaignNavName[active="true"] {color:#131A2C; font-weight:600;}
QLabel#campaignNavBadge {background:#DCE1EC; color:#3A4258; border-radius:10px; padding:0 8px; min-width:12px; font-size:11px; font-weight:600;}
QLabel#campaignNavBadge[active="true"] {background:#E8EBFD; color:#2F45D8;}
QLabel#campaignNavBadge[tone="running"] {background:#2F45D8; color:#FFFFFF;}
QPushButton#newCampaignNav {background:transparent; color:#2F45D8; border:1px dashed #CDD3E0; border-radius:8px; padding:8px 10px; text-align:left; font-weight:600;}
QPushButton#newCampaignNav:hover {background:#FFFFFF; border-color:#2F45D8;}
QPushButton#backLink {background:transparent; color:#2F45D8; border:0; padding:2px 0; text-align:left; font-weight:600;}
QPushButton#backLink:hover {color:#2537B8;}
QLabel#title {font-family:"@TITLEFONT@"; font-size:27px; font-weight:700; color:#131A2C;}
QLabel#sectionTitle {font-family:"@TITLEFONT@"; font-size:18px; font-weight:700; color:#131A2C;}
QLabel#muted {color:#59627A;}
QLabel#mono {font-family:"@MONOFONT@"; color:#767E96; font-size:12px;}
QLabel#columnChips {color:#3A4258; background:#F0F2F6; border-radius:10px; padding:8px 10px; font-size:11px; font-weight:600;}
QLabel#importTitle, QLabel#portfolioState, QLabel#fieldLabel {font-weight:600; color:#131A2C;}
QLabel#uploadIcon {background:#E8EBFD; color:#2F45D8; border:1px solid #CDD3E0; border-radius:25px; font-family:"@TITLEFONT@"; font-size:25px; font-weight:600;}
QFrame#card, QFrame#resultsCard {background:#FFFFFF; border:1px solid #E1E5EE; border-radius:12px;}
QFrame#drawerHead, QFrame#drawerFoot {background:#FFFFFF; border:0;}
QFrame#drawerHead {border-bottom:1px solid #E1E5EE;}
QFrame#drawerFoot {border-top:1px solid #E1E5EE;}
QScrollArea#drawerScroll, QWidget#drawerBody {background:#FFFFFF; border:0;}
QPushButton#drawerClose {background:#F0F2F6; color:#59627A; border:0; border-radius:8px; min-width:30px; max-width:30px; min-height:30px; max-height:30px; padding:0;}
QPushButton#drawerClose:hover {background:#E8EBFD; color:#131A2C;}
QLabel#resultDetailName {font-family:"@TITLEFONT@"; color:#131A2C; font-size:21px; font-weight:700;}
QLabel#detailHeading {color:#59627A; font-size:12px; font-weight:600; padding-top:4px;}
QLabel#shortcutHint {color:#767E96; font-size:11px;}
QFrame#noticeCard {background:#FFFFFF; border:1px solid #E1E5EE; border-radius:10px;}
QLabel#noticeTitle {color:#131A2C; font-size:14px; font-weight:600;}
QLabel#noticeDue {font-family:"@TITLEFONT@"; color:#131A2C; font-size:22px; font-weight:700;}
QLabel#readingText {font-size:12px; font-weight:600; color:#59627A;}
QLabel#evolutionSummary {font-size:13px; font-weight:600; color:#131A2C; padding-top:6px;}
QLabel#evolutionReason {font-size:12px; color:#59627A; padding-left:2px;}
QLabel#readingText[tone="success"] {color:#1A6A80;}
QLabel#readingText[tone="warning"] {color:#B01F48;}
QLabel#readingText[tone="info"] {color:#2F45D8;}
QLabel#readingBadge {background:#EBEEF4; color:#59627A; border-radius:10px; padding:3px 9px; font-size:12px; font-weight:600;}
QLabel#readingBadge[tone="success"] {background:#DFF1F6; color:#1A6A80;}
QLabel#readingBadge[tone="warning"] {background:#FCE5EB; color:#B01F48;}
QLabel#readingBadge[tone="info"] {background:#E8EBFD; color:#2F45D8;}
QLabel#formError {color:#B01F48; font-weight:600;}
QPlainTextEdit#resultDetailError {background:#FCE5EB; color:#131A2C; border:0; border-radius:8px; padding:8px;}
QFrame#importZone {background:#F7F8FB; border:2px dashed #CDD3E0; border-radius:14px; min-height:180px;}
QFrame#importZone:hover {background:#FFFFFF; border-color:#2F45D8;}
QFrame#importZone[dragActive="true"] {background:#E8EBFD; border:2px solid #2F45D8;}
QFrame#importZone[loaded="true"] {background:#F2FAFC; border-color:#9CCBD8;}
QFrame#campaignEmpty {background:#FFFFFF; border:1px dashed #CDD3E0; border-radius:14px;}
QFrame#campaignCard {background:#FFFFFF; border:1px solid #E1E5EE; border-radius:12px;}
QFrame#campaignCard:hover {border-color:#2F45D8;}
QLabel#campaignCardTitle {font-family:"@TITLEFONT@"; color:#131A2C; font-size:16px; font-weight:700;}
QPushButton#dangerText {background:transparent; color:#B01F48; border:0; padding:7px 9px;}
QPushButton#dangerText:hover {background:#FCE5EB;}
QFrame#actionToast {background:#FFFFFF; border:1px solid #E1E5EE; border-radius:12px;}
QFrame#actionToast[tone="warning"] {border-color:#F2C2CF;}
QLabel#toastMessage {color:#131A2C; background:transparent; font-size:13px; font-weight:700;}
QLabel#toastDetail {color:#59627A; background:transparent; font-size:12px;}
QPushButton#toastOpen {background:transparent; color:#2F45D8; border:0; padding:4px 0 0 0; font-weight:700; text-align:left;}
QPushButton#toastOpen:hover {color:#1F2FA8; text-decoration:underline;}
QPushButton#toastDismiss {background:transparent; border:0; border-radius:6px;}
QPushButton#toastDismiss:hover {background:#EEF0F5;}
QPushButton#fieldChip {background:#F2F4F8; color:#2F45D8; border:1px solid #E1E5EE; border-radius:8px; padding:4px 7px; font-family:"@MONOFONT@"; font-size:12px;}
QPushButton#fieldChip:hover {background:#E8EBFD; border-color:#C9D0F7;}
QPushButton#headerAccent {background:#E8EBFD; color:#2F45D8; border:1px solid #C9D0F7; border-radius:10px; padding:9px 15px 9px 12px; font-weight:700;}
QPushButton#headerAccent:hover {background:#DCE1FB; border-color:#AEB8F2;}
QPushButton#headerAccent:disabled {background:#F2F4F8; color:#9AA1B4; border-color:#E1E5EE;}
QFrame#activity {background:#FFFFFF; border:1px solid #E1E5EE; border-radius:12px;}
QPushButton#consoleToggle {background:#F7F8FB; color:#3A4258; border:1px solid #E1E5EE; border-radius:8px; padding:6px 10px; font-size:11px; font-weight:600;}
QPushButton#consoleToggle:hover {background:#E8EBFD; border-color:#2F45D8;}
QPlainTextEdit#liveConsole {background:#131A2C; color:#E8EBF4; border:0; border-radius:10px; padding:10px; font-family:"@MONOFONT@"; font-size:11px;}
QFrame#accountRow {background:#FFFFFF; border:1px solid #E1E5EE; border-radius:10px;}
QLabel#accountName {color:#131A2C; font-weight:600;}
QLabel#runHint {color:#A85600; font-size:12px;}
QLabel#mailBody {background:#FFFFFF; color:#26304A; padding:18px 20px; font-size:13px;}
QLabel#mailNote {background:#F7F8FB; color:#59627A; padding:12px 20px; border-top:1px solid #E1E5EE; border-bottom-left-radius:12px; border-bottom-right-radius:12px;}
QLabel#sessionTitle {font-size:15px; font-weight:600; color:#131A2C;}
QLabel#collectBig {font-family:"@TITLEFONT@"; font-size:34px; font-weight:700; color:#131A2C;}
QLabel#collectTotal {font-size:15px; color:#3A4258; padding-bottom:5px;}
QLabel#collectCount {color:#3A4258;}
QLabel#collectCount[kind="retry"] {color:#B01F48;}
QLabel#eventsHeading {font-weight:600; color:#131A2C; padding:14px 20px; border-bottom:1px solid #E1E5EE;}
QFrame#portfolioCheck {background:#F7F8FB; border:1px solid #E1E5EE; border-radius:10px;}
QFrame#portfolioCheck[tone="warning"] {background:#FFF7EC; border-color:#F3D2A8;}
QLabel#portfolioCheckTitle {color:#131A2C; font-weight:600;}
QDialog {background:#FFFFFF; color:#131A2C;}
QDialog QLabel {color:#131A2C;}
QDialog QPushButton#passwordVisibility {background:#F7F8FB; color:#131A2C; border:1px solid #CDD3E0; border-radius:8px; padding:6px 10px; min-width:66px;}
QDialog QPushButton#passwordVisibility:hover {background:#E8EBFD; border-color:#2F45D8;}
QPlainTextEdit#diagnosticLog {background:#F7F8FB; color:#26304A; border:1px solid #E1E5EE; border-radius:10px; padding:12px; font-family:"@MONOFONT@"; font-size:11px;}
QPushButton {background:#FFFFFF; color:#131A2C; border:1px solid #CDD3E0; border-radius:8px; padding:8px 13px; min-height:20px; font-weight:600;}
QPushButton:hover {background:#F7F8FB; border-color:#B7BFD1;}
QPushButton:pressed {background:#E9ECF3;}
QPushButton:focus {border:2px solid #2F45D8; padding:7px 12px;}
QPushButton#primary, QPushButton#runCollection {background:#2F45D8; color:#FFFFFF; border:1px solid #2F45D8; font-weight:600;}
QPushButton#primary:hover, QPushButton#runCollection:hover {background:#2537B8; border-color:#2537B8;}
QPushButton#utility, QPushButton#outline {background:#FFFFFF; color:#131A2C; border:1px solid #CDD3E0;}
QPushButton#utility:hover, QPushButton#outline:hover {background:#F7F8FB;}
QPushButton#textButton {background:transparent; color:#2F45D8; border:0; padding:5px 0; text-align:left;}
QPushButton#textButton:hover {color:#2537B8;}
QPushButton:disabled,QPushButton#primary:disabled,QPushButton#runCollection:disabled,QPushButton#utility:disabled {color:#8E96AD; background:#E9ECF3; border-color:#E9ECF3;}
QLineEdit,QSpinBox,QComboBox {background:#FFFFFF; color:#131A2C; border:1px solid #CDD3E0; border-radius:8px; padding:7px 10px; min-height:20px; selection-background-color:#E8EBFD; selection-color:#131A2C;}
QLineEdit:hover,QSpinBox:hover,QComboBox:hover {border-color:#9AA3BA;}
QLineEdit:focus,QSpinBox:focus,QComboBox:focus,QComboBox:on {border:1px solid #2F45D8;}
QLineEdit:disabled,QSpinBox:disabled,QComboBox:disabled {background:#E9ECF3; color:#8E96AD;}
QComboBox {padding-right:34px;}
QComboBox::drop-down {subcontrol-origin:padding; subcontrol-position:top right; width:32px; border:0; background:transparent;}
QComboBox::down-arrow {image:url(@ASSETS@/chevron-down.svg); width:14px; height:14px;}
QComboBox QAbstractItemView {background:#FFFFFF; color:#131A2C; border:1px solid #E1E5EE; padding:5px; outline:0; selection-background-color:#E8EBFD; selection-color:#131A2C;}
QComboBox QAbstractItemView::item {min-height:31px; padding:5px 10px; border-radius:6px;}
QSpinBox {padding-right:30px;}
QSpinBox::up-button,QSpinBox::down-button {subcontrol-origin:border; width:26px; border:0; background:transparent;}
QSpinBox::up-button {subcontrol-position:top right;}
QSpinBox::down-button {subcontrol-position:bottom right;}
QSpinBox::up-arrow {image:url(@ASSETS@/chevron-up.svg); width:11px; height:11px;}
QSpinBox::down-arrow {image:url(@ASSETS@/chevron-down.svg); width:11px; height:11px;}
QCheckBox {spacing:8px;}
QFrame#browserShell {background:#FFFFFF; border:1px solid #E1E5EE; border-radius:14px;}
QFrame#browserToolbar {background:#10162A; border:0; border-top-left-radius:13px; border-top-right-radius:13px;}
QPushButton#browserBack,QPushButton#browserTool {background:#1B2340; color:#E8EBF4; border:1px solid #283048; border-radius:8px; padding:5px 10px; min-height:18px;}
QPushButton#browserBack:hover,QPushButton#browserTool:hover {background:#283048;}
QPushButton#browserSessionTab {background:transparent; color:#A3ABC1; border:0; border-radius:8px; padding:7px 12px; min-height:20px; font-size:12px; text-align:left;}
QPushButton#browserSessionTab:hover {background:#1B2340; color:#FFFFFF;}
QPushButton#browserSessionTab:checked {background:#1B2340; color:#FFFFFF; border-bottom:2px solid #8291FF; padding-bottom:5px;}
QLabel#browserLock {color:#5BC3DC; font-size:9px; padding-left:7px;}
QLabel#browserAddress {font-family:"@MONOFONT@"; background:#151B2D; color:#E8EBF4; border:1px solid #283048; border-radius:8px; padding:7px 10px; font-size:11px;}
QPushButton#browserZoom {background:#151B2D; color:#E8EBF4; border:1px solid #283048; border-radius:8px; padding:5px 9px; min-height:18px; min-width:48px;}
QTabWidget#browserTabs::pane {background:#FFFFFF; border:0;}
QScrollArea#browserScroll {background:#FFFFFF; border:0; border-bottom-left-radius:13px; border-bottom-right-radius:13px;}
QTableWidget {background:#FFFFFF; alternate-background-color:#FFFFFF; border:0; selection-background-color:#E8EBFD; selection-color:#131A2C; outline:0;}
QTableWidget::item {padding:0 8px; border-bottom:1px solid #EEF0F5;}
QTableWidget::item:hover {background:#F7F8FB;}
QTableWidget::item:selected {background:#E8EBFD; color:#131A2C;}
QTableWidget::indicator {width:18px; height:18px; border:1.5px solid #CDD3E0; border-radius:5px; background:#FFFFFF;}
QTableWidget::indicator:checked {background:#2F45D8; border-color:#2F45D8; image:url(@ASSETS@/icons/check-white.svg);}
QTableWidget::indicator:disabled {border-color:#E9ECF3; background:#F7F8FB;}
QHeaderView {background:#F7F8FB;}
QHeaderView::section {background:#F7F8FB; color:#59627A; border:0; border-bottom:1px solid #E1E5EE; padding:9px 8px; font-size:11px; font-weight:600;}
QTableCornerButton::section,QAbstractScrollArea::corner {background:#FFFFFF; border:0;}
QScrollBar:vertical {background:transparent; width:10px; margin:2px; border-radius:5px;}
QScrollBar::handle:vertical {background:#CDD3E0; min-height:32px; border-radius:4px;}
QScrollBar:horizontal {background:transparent; height:10px; margin:2px;}
QScrollBar::handle:horizontal {background:#CDD3E0; min-width:32px; border-radius:4px;}
QScrollArea#browserScroll QScrollBar:horizontal {background:#E9ECF3; height:16px; margin:0; border:0;}
QScrollArea#browserScroll QScrollBar::handle:horizontal {background:#59627A; min-width:72px; margin:3px 5px; border-radius:4px;}
QScrollArea#browserScroll QScrollBar::handle:horizontal:hover {background:#2F45D8;}
QScrollBar::add-line,QScrollBar::sub-line {width:0; height:0;}
QScrollBar::add-page,QScrollBar::sub-page {background:transparent;}
QProgressBar {background:#E9ECF3; border:0; border-radius:4px;}
QProgressBar::chunk {background:#2F45D8; border-radius:4px;}
QProgressBar[hasErrors="true"]::chunk {background:#B01F48;}
QPushButton {border-radius:10px; border-color:#E1E5EE;}
QPushButton:focus {border:2px solid #2F45D8;}
QPushButton#primary, QPushButton#runCollection {border-radius:10px; font-weight:700; padding:9px 16px;}
QWidget#headerActions QPushButton {min-height:22px; padding:9px 16px;}
QPushButton#accent {background:#E8EBFD; color:#2F45D8; border:1px solid #C9D0F7; font-weight:700;}
QPushButton#accent:hover {background:#DCE1FB; border-color:#AEB8F2;}
QPushButton#accent:disabled {background:#F2F4F8; color:#9AA1B4; border-color:#E1E5EE;}
QPushButton#linkStrong, QPushButton#linkMuted {background:transparent; border:0; padding:2px 0; min-height:16px; text-align:left; color:#2F45D8; font-weight:700;}
QPushButton#linkMuted {color:#59627A; font-weight:600;}
QPushButton#linkStrong:hover, QPushButton#linkMuted:hover {color:#1F2FA8;}
QLineEdit,QSpinBox,QComboBox {border-color:#E1E5EE; border-radius:10px;}
QLineEdit#searchField {padding:8px 10px 8px 4px;}
QCheckBox#plainCheck {color:#3A4258;}
QCheckBox::indicator {width:16px; height:16px; border:1.5px solid #CDD3E0; border-radius:4px; background:#FFFFFF;}
QCheckBox::indicator:checked {background:#2F45D8; border-color:#2F45D8; image:url(@ASSETS@/icons/check-white.svg);}
QLabel#title {font-size:28px;}
QPushButton#backLink {font-size:12px; padding:0;}
QLabel#railSection {background:transparent; color:#59627A; font-size:11px; font-weight:600; padding:0 10px 6px;}
QPushButton#nav {border-radius:10px; padding:9px 10px;}
QPushButton#campaignNavItem {border-radius:10px; min-height:50px;}
QPushButton#campaignNavItem:checked {background:#FFFFFF; border-color:#E1E5EE;}
QLabel#campaignNavBadge {background:#DCE1EC; color:#3A4258;}
QLabel#campaignNavBadge[active="true"] {background:#E8EBFD; color:#2F45D8;}
QLabel#campaignNavBadge[tone="running"] {background:#2F45D8; color:#FFFFFF;}
QProgressBar#campaignNavProgress {background:#DCE1EC; border:0; border-radius:2px;}
QProgressBar#campaignNavProgress[active="true"] {background:#E1E5EE;}
QProgressBar#campaignNavProgress::chunk {background:#2F45D8; border-radius:2px;}
QPushButton#newCampaignNav {border:1px dashed #C9D0F7; border-radius:10px; padding:9px 10px;}
QFrame#railUser {background:transparent; border:0; border-top:1px solid #DCE1EC;}
QLabel#railAvatar {background:#DCE1EC; color:#3A4258; border-radius:14px; font-weight:700; font-size:11px;}
QLabel#railUserName {background:transparent; font-weight:600; color:#131A2C;}
QLabel#railUserDetail {background:transparent; color:#59627A; font-size:11px;}
QFrame#campaignTabs {background:transparent; border:0; border-bottom:1px solid #E1E5EE;}
QPushButton#campaignTab {background:transparent; border:0; border-bottom:2px solid transparent; border-radius:0; padding:0; min-height:40px;}
QPushButton#campaignTab:hover {background:transparent; border-bottom-color:#C9D0F7;}
QPushButton#campaignTab:checked {border-bottom:2px solid #2F45D8;}
QPushButton#campaignTab:focus {border:0; border-bottom:2px solid #2F45D8;}
QLabel#campaignTabTitle {background:transparent; color:#59627A; font-weight:600;}
QLabel#campaignTabTitle[active="true"] {color:#131A2C; font-weight:700;}
QLabel#campaignTabStep {background:#DCE1EC; color:#3A4258; border-radius:10px; font-size:11px; font-weight:700;}
QLabel#campaignTabStep[active="true"] {background:#2F45D8; color:#FFFFFF;}
QLabel#campaignTabBadge {background:#DCE1EC; color:#3A4258; border-radius:10px; padding:0 8px; font-size:11px; font-weight:600;}
QLabel#campaignTabBadge[tone="active"] {background:#E8EBFD; color:#2F45D8;}
QLabel#campaignTabBadge[tone="danger"] {background:#FCE5EB; color:#B01F48;}
QLabel#campaignTabBadge[tone="running"] {background:#2F45D8; color:#FFFFFF;}
QPushButton#filterChip {background:#FFFFFF; border:1px solid #E1E5EE; border-radius:16px; padding:0; min-height:30px;}
QPushButton#filterChip:hover {border-color:#9AA3BA;}
QPushButton#filterChip:checked {background:#131A2C; border-color:#131A2C;}
QPushButton#filterChip:focus {border:1px solid #2F45D8;}
QLabel#filterChipLabel {background:transparent; color:#3A4258; font-size:12px; font-weight:600;}
QLabel#filterChipCount {background:transparent; color:#3A4258; font-family:"@MONOFONT@"; font-size:12px;}
QLabel#filterChipLabel[active="true"], QLabel#filterChipCount[active="true"] {color:#FFFFFF;}
QFrame#resultsCard, QFrame#card {border-radius:14px;}
QFrame#cardBar {background:transparent; border:0; border-bottom:1px solid #EEF0F5;}
QFrame#cardFoot {background:transparent; border:0; border-top:1px solid #EEF0F5;}
QFrame#dossierSheet {background:#FFFFFF; border:1px solid #E1E5EE; border-radius:14px;}
QFrame#dossierSheet QFrame#drawerHead, QFrame#dossierSheet QFrame#drawerFoot {background:transparent;}
QFrame#dossierSheet QFrame#drawerHead {border-bottom:1px solid #EEF0F5;}
QFrame#dossierSheet QFrame#drawerFoot {border-top:1px solid #EEF0F5;}
QLabel#resultDetailName {font-size:18px;}
QFrame#noticeCard {border-radius:12px;}
QFrame#noticeRule {background:#EEF0F5; border:0; min-height:1px; max-height:1px; margin-top:4px;}
QLabel#noticeDue {font-size:24px;}
QLabel#evolutionSummary {padding-top:2px;}
QPushButton#settingsLink {background:transparent; color:#3A4258; border:1px solid transparent; border-radius:9px; padding:9px 12px; text-align:left; font-weight:500;}
QPushButton#settingsLink:hover {background:rgba(255,255,255,0.6);}
QPushButton#settingsLink:checked {background:#FFFFFF; color:#131A2C; border-color:#E1E5EE; font-weight:600;}
QFrame#settingsSection {background:#FFFFFF; border:1px solid #E1E5EE; border-radius:14px;}
QFrame#settingsHead {background:transparent; border:0; border-bottom:1px solid #EEF0F5;}
QLabel#settingsTitle {font-family:"@TITLEFONT@"; font-size:16px; font-weight:700; color:#131A2C;}
QLabel#settingsPreview {background:#F7F8FB; color:#3A4258; border-radius:10px; padding:12px 14px;}
QFrame#settingsSection QFrame#accountRow {background:transparent; border:0; border-bottom:1px solid #EEF0F5; border-radius:0;}
QFrame#settingsSection QPlainTextEdit#diagnosticLog {background:transparent; border:0; border-radius:0; padding:12px 20px 16px; color:#3A4258; font-size:12px;}
QLabel#readingBadge[tone="attention"] {background:#FFF0DC; color:#A85600;}
QFrame#sendBar {background:#131A2C; border:0; border-bottom-left-radius:13px; border-bottom-right-radius:13px;}
QLabel#sendBarTitle {background:transparent; color:#FFFFFF; font-weight:600;}
QLabel#sendBarDetail {background:transparent; color:#C3CADB;}
QPushButton#sendBarButton {background:#FFFFFF; color:#131A2C; border:0; border-radius:10px; padding:9px 16px; font-weight:700;}
QPushButton#sendBarButton:hover {background:#E8EBFD;}
QPushButton#sendBarButton:disabled {background:#2A3350; color:#8E96AD;}
QTableWidget#mailList::item {border-bottom:1px solid #EEF0F5; padding:0 6px;}
QPushButton[compact="true"], QPushButton#primary[compact="true"], QPushButton#accent[compact="true"] {padding:6px 12px; min-height:18px; font-size:12px; border-radius:9px;}
QLabel#attachmentLine {color:#3A4258;}
QDialog#accountDialog {background:#FFFFFF;}
QLabel#dialogTitle {font-family:"@TITLEFONT@"; font-size:19px; font-weight:700; color:#131A2C;}
QLabel#dialogAlert {background:#FCE5EB; color:#8E1638; border-radius:10px; padding:10px 12px;}
QLabel#fieldHint {color:#59627A; font-size:12px;}
QToolButton#passwordVisibility {background:transparent; border:0; border-radius:6px;}
QToolButton#passwordVisibility:hover {background:#EEF0F5;}
QFrame#accountsEmpty {background:transparent; border:0;}
QFrame#settingsSection QFrame#accountRow:hover {background:#F7F8FB;}
QFrame#connectionPanel {background:#FFFFFF; border:1px solid #E1E5EE; border-radius:14px;}
QFrame#connectionPanel[tone="warning"] {background:#FFF8EE; border:1.5px solid #A85600;}
QFrame#connectionPanel[tone="error"] {background:#FDF2F5; border:1.5px solid #B01F48;}
QLabel#connectionDot {border-radius:5px; background:#9AA6C0;}
QLabel#connectionDot[tone="connected"] {background:#1A6A80;}
QLabel#connectionDot[tone="working"] {background:#2F45D8;}
QLabel#connectionDot[tone="warning"] {background:#A85600;}
QLabel#connectionDot[tone="error"] {background:#B01F48;}
QFrame#wizardStep {background:#FFFFFF; border:1px solid #E1E5EE; border-radius:12px;}
QFrame#wizardStep[stepState="active"] {border:2px solid #2F45D8;}
QFrame#wizardStep[stepState="todo"] {background:#F7F8FB;}
QLabel#wizardDot {background:#DCE1EC; color:#3A4258; border-radius:13px; font-weight:700;}
QLabel#wizardDot[stepState="active"] {background:#2F45D8; color:#FFFFFF;}
QLabel#wizardDot[stepState="done"] {background:#1A6A80;}
QLabel#wizardTitle {background:transparent; font-weight:700; color:#131A2C;}
QLabel#wizardDetail {background:transparent; color:#59627A;}
QFrame#wizardStep[stepState="todo"] QLabel#wizardTitle {color:#59627A;}
QFrame#wizardCard {background:#FFFFFF; border:1px solid #E1E5EE; border-radius:14px;}
QFrame#wizardRule {background:#EEF0F5; border:0; min-height:1px; max-height:1px;}
QPushButton#yearChip {background:#FFFFFF; color:#3A4258; border:1px solid #E1E5EE; border-radius:18px; padding:7px 16px; min-height:20px;}
QPushButton#yearChip:hover {border-color:#9AA3BA;}
QPushButton#yearChip:checked {background:#131A2C; color:#FFFFFF; border-color:#131A2C; font-weight:700;}
QLineEdit#monoField {font-family:"@MONOFONT@";}
QLabel#outputTree {background:#F7F8FB; color:#3A4258; border-radius:10px; padding:12px 14px; font-size:12px;}
QLabel#fact {background:transparent; color:#131A2C;}
QLabel#factWarning {background:transparent; color:#A85600;}
QLabel#warningNote {background:#FFF0DC; color:#6B3A00; border-radius:10px; padding:10px 12px;}
QFrame#campaignCard {border-radius:14px;}
QFrame#campaignCard:hover {border-color:#C9D0F7;}
QFrame#campaignCard[archived="true"] {background:#F7F8FB;}
QLabel#campaignCardTitle {font-size:18px;}
QToolButton#cardMenu {background:#FFFFFF; border:1px solid #E1E5EE; border-radius:8px; min-width:32px; max-width:32px; min-height:32px; max-height:32px;}
QToolButton#cardMenu:hover {background:#F7F8FB;}
QToolButton#cardMenu::menu-indicator {image:none; width:0;}
QMenu {background:#FFFFFF; border:1px solid #E1E5EE; border-radius:10px; padding:6px;}
QMenu::item {padding:7px 14px; border-radius:6px; color:#131A2C;}
QMenu::item:selected {background:#E8EBFD;}
QMenu::separator {height:1px; background:#EEF0F5; margin:4px 6px;}
QFrame#liveFeed {background:#131A2C; border:0; border-radius:14px;}
QLabel#liveFeedTitle {background:transparent; color:#FFFFFF; font-weight:700; font-size:14px; padding-bottom:6px;}
QLabel#liveFeedLine {background:transparent; color:#E8EBF4; font-family:"@MONOFONT@"; font-size:12px;}
QLabel#liveFeedTime {background:transparent; color:#9AA6C0; font-family:"@MONOFONT@"; font-size:12px;}
QFrame#liveFeedRow {background:transparent; border:0;}
QFrame#todoRow {background:transparent; border:0; border-bottom:1px solid #EEF0F5;}
QLabel#eventsHeading {font-size:14px; font-weight:700; padding:14px 18px; border-bottom:1px solid #EEF0F5;}
QLabel#collectTotal {font-size:14px;}
QLabel#mailNote {background:transparent; border-top:1px solid #EEF0F5; color:#59627A;}
QLabel#mailBody {background:transparent; color:#131A2C;}
"""

for _kind, (_background, _foreground) in SITUATION_COLORS.items():
    STYLE += f'QLabel#situationChip[kind="{_kind}"] {{background:{_background}; color:{_foreground}; border-radius:11px; padding:3px 10px; font-size:12px; font-weight:600;}}\n'
