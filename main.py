import sys
import json
import shutil
from pathlib import Path

from PyQt5 import QtCore, QtWidgets, QtGui
import fitz  # PyMuPDF


class TocItemWidget(QtWidgets.QTreeWidgetItem):
    def __init__(self, title, page):
        super().__init__([title, str(page)])
        self.setFlags(self.flags() | QtCore.Qt.ItemIsEditable | QtCore.Qt.ItemIsDragEnabled | QtCore.Qt.ItemIsDropEnabled)

    def title(self):
        return self.text(0)

    def page(self):
        text = self.text(1).strip()
        if "-" in text:
            try:
                start, end = map(int, text.split("-"))
                return f"{start}-{end}"
            except Exception:
                return 1
        try:
            return int(text)
        except Exception:
            return 1


class PDFTocEditor(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('PDF TOC Editor')
        self.resize(900, 600)

        # Window icon
        self.setWindowIcon(QtGui.QIcon('icon.ico'))

        self.pdf_path = None
        self.doc = None

        # Undo/Redo stacks
        self.undo_stack = []
        self.redo_stack = []
        self.is_restoring = False  # Prevent recursion

        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        layout = QtWidgets.QVBoxLayout(central)

        toolbar = QtWidgets.QHBoxLayout()
        layout.addLayout(toolbar)

        open_btn = QtWidgets.QPushButton('Open PDF')
        open_btn.clicked.connect(self.open_pdf)
        toolbar.addWidget(open_btn)

        save_btn = QtWidgets.QPushButton('Save to PDF')
        save_btn.clicked.connect(self.save_pdf)
        toolbar.addWidget(save_btn)

        # Add Save As button
        save_as_btn = QtWidgets.QPushButton('Save As...')
        save_as_btn.clicked.connect(self.save_pdf_as)
        toolbar.addWidget(save_as_btn)

        import_btn = QtWidgets.QPushButton('Import (.json)')
        import_btn.clicked.connect(self.import_toc)
        toolbar.addWidget(import_btn)

        export_btn = QtWidgets.QPushButton('Export (.json)')
        export_btn.clicked.connect(self.export_toc)
        toolbar.addWidget(export_btn)

        # Undo/Redo buttons
        undo_btn = QtWidgets.QPushButton('Undo')
        redo_btn = QtWidgets.QPushButton('Redo')
        undo_btn.clicked.connect(self.undo)
        redo_btn.clicked.connect(self.redo)
        toolbar.addWidget(undo_btn)
        toolbar.addWidget(redo_btn)

        toolbar.addStretch()

        splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        layout.addWidget(splitter)

        self.tree = QtWidgets.QTreeWidget()
        self.tree.setColumnCount(2)
        self.tree.setHeaderLabels(['Title', 'Page'])
        self.tree.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.tree.setEditTriggers(QtWidgets.QAbstractItemView.DoubleClicked | QtWidgets.QAbstractItemView.SelectedClicked)
        self.tree.setColumnWidth(0, 400)
        # drag-drop
        self.tree.setDragDropMode(QtWidgets.QAbstractItemView.InternalMove)
        self.tree.setDefaultDropAction(QtCore.Qt.MoveAction)
        splitter.addWidget(self.tree)

        right = QtWidgets.QWidget()
        right_layout = QtWidgets.QVBoxLayout(right)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)

        add_sibling_btn = QtWidgets.QPushButton('Add Sibling')
        add_child_btn = QtWidgets.QPushButton('Add Child')
        delete_btn = QtWidgets.QPushButton('Delete Selected')
        delete_all_btn = QtWidgets.QPushButton('Delete All')
        move_up_btn = QtWidgets.QPushButton('Move Up')
        move_down_btn = QtWidgets.QPushButton('Move Down')

        for w in [add_sibling_btn, add_child_btn, delete_btn, delete_all_btn, move_up_btn, move_down_btn]:
            w.setMinimumWidth(170)
            w.setMinimumHeight(50)  # Increased height
            right_layout.addWidget(w)

        add_sibling_btn.clicked.connect(lambda: self.add_item(child=False))
        add_child_btn.clicked.connect(lambda: self.add_item(child=True))
        delete_btn.clicked.connect(self.delete_item)
        delete_all_btn.clicked.connect(self.delete_all_items)
        move_up_btn.clicked.connect(lambda: self.move_item(-1))
        move_down_btn.clicked.connect(lambda: self.move_item(1))

        right_layout.addStretch()

        self.status = self.statusBar()

        # Install event filter for Ctrl+A
        self.tree.installEventFilter(self)

        # Track changes for undo/redo
        self.tree.itemChanged.connect(self.save_undo_state)
        self.tree.model().rowsInserted.connect(self.save_undo_state)
        self.tree.model().rowsRemoved.connect(self.save_undo_state)
        self.tree.model().rowsMoved.connect(self.save_undo_state)

    # --- Undo/Redo logic ---

    def save_undo_state(self, *args, **kwargs):
        if self.is_restoring:
            return
        toc = self.tree_to_toc()
        if not self.undo_stack or self.undo_stack[-1] != toc:
            self.undo_stack.append(json.loads(json.dumps(toc)))  # Deep copy
            self.redo_stack.clear()

    def restore_toc_state(self, toc):
        self.is_restoring = True
        self.populate_tree_from_toc(toc)
        self.is_restoring = False

    def undo(self):
        if len(self.undo_stack) < 2:
            return
        current = self.undo_stack.pop()
        self.redo_stack.append(current)
        prev = self.undo_stack[-1]
        self.restore_toc_state(prev)
        self.status.showMessage('Undo')

    def redo(self):
        if not self.redo_stack:
            return
        next_state = self.redo_stack.pop()
        self.undo_stack.append(next_state)
        self.restore_toc_state(next_state)
        self.status.showMessage('Redo')

    # --- End Undo/Redo logic ---

    def open_pdf(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, 'Open PDF', '', 'PDF files (*.pdf)')
        if not path:
            return
        self.load_pdf(path)

    def load_pdf(self, path):
        try:
            doc = fitz.open(path)
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, 'Error', f'Could not open PDF:\n{e}')
            return

        self.pdf_path = path
        self.doc = doc
        self.setWindowTitle(f'PDF TOC Editor — {Path(path).name}')
        self.tree.clear()

        toc = doc.get_toc(simple=False)
        self.populate_tree_from_toc(toc)
        self.status.showMessage(f'Loaded: {path} — pages: {len(doc)} — toc entries: {len(toc)}')

    def populate_tree_from_toc(self, toc):
        self.tree.clear()
        if not toc:
            return

        stack = []
        for entry in toc:
            try:
                level, title, page = entry[:3]
            except Exception:
                continue
            item = TocItemWidget(title, page)
            if level <= 1:
                self.tree.addTopLevelItem(item)
                stack = [item]
            else:
                parent_level_index = level - 2
                if parent_level_index < len(stack):
                    parent = stack[parent_level_index]
                    parent.addChild(item)
                    if len(stack) >= level:
                        stack[level - 1:level] = [item]
                    else:
                        stack.append(item)
                else:
                    self.tree.addTopLevelItem(item)
                    stack = [item]
        # Save state after loading
        if not self.is_restoring:
            self.save_undo_state()

    def tree_to_toc(self):
        toc = []
        def recurse(node, level):
            for i in range(node.childCount()):
                child = node.child(i)
                toc.append([level, child.title(), child.page()])
                recurse(child, level + 1)

        for i in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(i)
            toc.append([1, top.title(), top.page()])
            recurse(top, 2)
        return toc

    def add_item(self, child=False):
        sel = self.tree.currentItem()
        if sel is None:
            parent = None
        else:
            parent = sel if child else sel.parent()
        item = TocItemWidget("New Title", 1)
        if parent is None:
            self.tree.addTopLevelItem(item)
        else:
            parent.addChild(item)
            parent.setExpanded(True)
        self.tree.setCurrentItem(item)
        self.tree.editItem(item, 0)
        self.save_undo_state()

    def delete_item(self):
        selected_items = self.tree.selectedItems()
        if not selected_items:
            return
        # Sort by depth (deepest first)
        def item_depth(item):
            d = 0
            while item.parent():
                d += 1
                item = item.parent()
            return d
        for item in sorted(selected_items, key=item_depth, reverse=True):
            parent = item.parent()
            if parent is None:
                idx = self.tree.indexOfTopLevelItem(item)
                self.tree.takeTopLevelItem(idx)
            else:
                parent.removeChild(item)
        self.save_undo_state()

    def delete_all_items(self):
        self.tree.clear()
        self.status.showMessage('All TOC entries deleted.')
        self.save_undo_state()

    def move_item(self, direction):
        item = self.tree.currentItem()
        if item is None:
            return
        parent = item.parent()
        if parent is None:
            idx = self.tree.indexOfTopLevelItem(item)
            new_idx = idx + direction
            if 0 <= new_idx < self.tree.topLevelItemCount():
                item_clone = self.clone_item(item)
                self.tree.takeTopLevelItem(idx)
                self.tree.insertTopLevelItem(new_idx, item_clone)
                self.tree.setCurrentItem(item_clone)
        else:
            idx = parent.indexOfChild(item)
            new_idx = idx + direction
            if 0 <= new_idx < parent.childCount():
                clone = self.clone_item(item)
                parent.takeChild(idx)
                parent.insertChild(new_idx, clone)
                self.tree.setCurrentItem(clone)
        self.save_undo_state()

    def clone_item(self, item):
        clone = TocItemWidget(item.title(), item.page())
        for i in range(item.childCount()):
            child = item.child(i)
            clone.addChild(self.clone_item(child))
        return clone

    def export_toc(self):
        if self.tree.topLevelItemCount() == 0:
            QtWidgets.QMessageBox.information(self, 'Export', 'No TOC entries to export.')
            return
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, 'Export TOC JSON', '', 'JSON files (*.json)')
        if not path:
            return
        toc = self.tree_to_toc()
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(toc, f, indent=2, ensure_ascii=False)
        self.status.showMessage(f'Exported TOC to {path}')

    def import_toc(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, 'Import TOC JSON', '', 'JSON files (*.json)')
        if not path:
            return
        try:
            with open(path, 'r', encoding='utf-8') as f:
                toc = json.load(f)
            self.populate_tree_from_toc(toc)
            self.status.showMessage(f'Imported TOC from {path}')
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, 'Error', f'Could not import TOC:\n{e}')

    def save_pdf(self):
        if self.doc is None or self.pdf_path is None:
            QtWidgets.QMessageBox.information(self, 'Save', 'Open a PDF first.')
            return
        # Save directly to the opened PDF
        toc = self.tree_to_toc()
        py_toc = []
        for level, title, page in toc:
            if isinstance(page, str) and "-" in str(page):
                try:
                    start, end = map(int, str(page).split("-"))
                    py_toc.append((int(level), str(title), int(start)))
                except Exception:
                    continue
            else:
                py_toc.append((int(level), str(title), int(page)))
        try:
            self.doc.set_toc(py_toc)
            self.doc.saveIncr()
            self.status.showMessage(f'Saved TOC to opened PDF')
            QtWidgets.QMessageBox.information(self, 'Saved', f'Saved TOC to opened PDF.')
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, 'Error', f'Could not save PDF:\n{e}')

    def save_pdf_as(self):
        if self.doc is None or self.pdf_path is None:
            QtWidgets.QMessageBox.information(self, 'Save As', 'Open a PDF first.')
            return
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, 'Save PDF As', '', 'PDF files (*.pdf)')
        if not path:
            return
        toc = self.tree_to_toc()
        py_toc = []
        for level, title, page in toc:
            if isinstance(page, str) and "-" in str(page):
                try:
                    start, end = map(int, str(page).split("-"))
                    py_toc.append((int(level), str(title), int(start)))
                except Exception:
                    continue
            else:
                py_toc.append((int(level), str(title), int(page)))
        try:
            # Copy original PDF to new path, then set TOC
            shutil.copyfile(self.pdf_path, path)
            doc = fitz.open(path)
            doc.set_toc(py_toc)
            doc.saveIncr()
            doc.close()
            self.status.showMessage(f'Saved TOC to {path}')
            QtWidgets.QMessageBox.information(self, 'Saved', f'Saved TOC to {path}.')
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, 'Error', f'Could not save PDF:\n{e}')

    def eventFilter(self, obj, event):
        if obj == self.tree and event.type() == QtCore.QEvent.KeyPress:
            # Ctrl+A: select all
            if event.key() == QtCore.Qt.Key_A and event.modifiers() & QtCore.Qt.ControlModifier:
                self.tree.clearSelection()
                for i in range(self.tree.topLevelItemCount()):
                    item = self.tree.topLevelItem(i)
                    self.select_all_recursive(item)
                return True
            # Delete key: delete selected
            if event.key() == QtCore.Qt.Key_Delete:
                self.delete_item()
                return True
            # Ctrl+Up: move up
            if event.key() == QtCore.Qt.Key_Up and event.modifiers() & QtCore.Qt.ControlModifier:
                self.move_item(-1)
                return True
            # Ctrl+Down: move down
            if event.key() == QtCore.Qt.Key_Down and event.modifiers() & QtCore.Qt.ControlModifier:
                self.move_item(1)
                return True
        return super().eventFilter(obj, event)

    def select_all_recursive(self, item):
        item.setSelected(True)
        for i in range(item.childCount()):
            self.select_all_recursive(item.child(i))


def main():
    app = QtWidgets.QApplication(sys.argv)
    win = PDFTocEditor()
    win.show()
    sys.exit(app.exec_())


if __name__ == '__main__':
    main()
