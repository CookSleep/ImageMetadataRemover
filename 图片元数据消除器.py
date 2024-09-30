import sys
import os
import threading
import requests
import tempfile  # 新增
from PyQt5.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QLabel, QPushButton,
    QFileDialog, QCheckBox, QLineEdit
)
from PyQt5.QtCore import (
    Qt, QMimeData, pyqtSignal, QObject, QByteArray, QBuffer,
    QIODevice, QUrl
)
from PyQt5.QtGui import QPixmap, QImage, QIcon
from PIL import Image
import io

def resource_path(relative_path):
    """获取资源文件的绝对路径，支持 PyInstaller"""
    try:
        # PyInstaller 创建的临时文件夹路径
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)

class SignalEmitter(QObject):
    signal = pyqtSignal(str)

class ImageProcessor(QWidget):
    def __init__(self):
        super().__init__()
        self.initUI()
        self.save_directory = ""
        self.save_option_checked_before = False
        self.temp_files = []  # 存储临时文件路径

    def initUI(self):
        self.setWindowTitle('图片元数据消除器')
        icon_path = resource_path("图片元数据消除器-3种尺寸.ico")
        self.setWindowIcon(QIcon(icon_path))  # 设置窗口图标
        self.setAcceptDrops(True)
        self.resize(400, 300)

        self.layout = QVBoxLayout()

        self.label = QLabel('将图片拖拽到此窗口')
        self.label.setAlignment(Qt.AlignCenter)
        self.layout.addWidget(self.label)

        self.save_checkbox = QCheckBox('保存处理后图片到指定目录')
        self.save_checkbox.stateChanged.connect(self.toggle_save_option)
        self.layout.addWidget(self.save_checkbox)

        self.dir_input = QLineEdit()
        self.dir_input.setPlaceholderText('点击选择目录')
        self.dir_input.setReadOnly(True)
        self.dir_input.mousePressEvent = self.choose_directory
        self.layout.addWidget(self.dir_input)
        self.dir_input.hide()

        self.copy_button = QPushButton('复制')
        self.copy_button.clicked.connect(self.copy_results)
        self.layout.addWidget(self.copy_button)

        # 新增的窗口置顶功能
        self.always_on_top_checkbox = QCheckBox('窗口置顶')
        self.always_on_top_checkbox.stateChanged.connect(self.toggle_always_on_top)
        self.layout.addWidget(self.always_on_top_checkbox)

        self.setLayout(self.layout)

        self.image_data_list = []
        self.processed_images = []
        self.signal_emitter = SignalEmitter()
        self.signal_emitter.signal.connect(self.update_ui)

    def toggle_always_on_top(self, state):
        if state == Qt.Checked:
            self.setWindowFlags(self.windowFlags() | Qt.WindowStaysOnTopHint)
        else:
            self.setWindowFlags(self.windowFlags() & ~Qt.WindowStaysOnTopHint)
        self.show()

    def toggle_save_option(self, state):
        if state == Qt.Checked:
            self.dir_input.show()
            if not self.save_directory and not self.save_option_checked_before:
                self.choose_directory()
                self.save_option_checked_before = True
            elif self.save_directory:
                self.dir_input.setText(self.save_directory)
        else:
            self.dir_input.hide()

    def choose_directory(self, event=None):
        directory = QFileDialog.getExistingDirectory(self, '选择保存目录', os.getcwd())
        if directory:
            self.save_directory = directory
            self.dir_input.setText(self.save_directory)

    def dragEnterEvent(self, event):
        if event.mimeData().hasImage() or event.mimeData().hasUrls():
            event.accept()
        else:
            event.ignore()

    def dropEvent(self, event):
        self.image_data_list = []
        if event.mimeData().hasUrls():
            urls = event.mimeData().urls()
            for url in urls:
                if url.isLocalFile():
                    path = url.toLocalFile()
                    self.image_data_list.append(('file', path))
                else:
                    url_str = url.toString()
                    self.image_data_list.append(('url', url_str))
        elif event.mimeData().hasImage():
            image = event.mimeData().imageData()
            self.image_data_list.append(('image', image))
        else:
            self.label.setText('不支持的文件类型')
            return

        if self.image_data_list:
            self.label.setText('正在处理...')
            threading.Thread(target=self.process_images).start()
        else:
            self.label.setText('不支持的文件类型')

    def process_images(self):
        self.processed_images = []
        threads = []
        for data_type, data in self.image_data_list:
            thread = threading.Thread(target=self.remove_metadata, args=(data_type, data))
            threads.append(thread)
            thread.start()

        for thread in threads:
            thread.join()

        self.signal_emitter.signal.emit('处理完成')

    def remove_metadata(self, data_type, data):
        try:
            if data_type == 'file':
                image = Image.open(data)
                image_format = image.format
            elif data_type == 'image':
                buffer = QBuffer()
                buffer.open(QIODevice.WriteOnly)
                data.save(buffer, "PNG")
                pil_data = buffer.data()
                image = Image.open(io.BytesIO(pil_data))
                image_format = image.format
            elif data_type == 'url':
                response = requests.get(data)
                image = Image.open(io.BytesIO(response.content))
                image_format = image.format
            else:
                return

            # 去除元数据
            data_no_exif = list(image.getdata())
            image_without_exif = Image.new(image.mode, image.size)
            image_without_exif.putdata(data_no_exif)

            if self.save_checkbox.isChecked() and self.save_directory:
                # 确保文件名有正确的扩展名
                if data_type == 'file':
                    filename = os.path.basename(data)
                elif data_type == 'url':
                    filename = os.path.basename(QUrl(data).path())
                    if not filename or not os.path.splitext(filename)[1]:
                        filename = 'image_{}.png'.format(len(self.processed_images) + 1)
                else:
                    filename = 'image_{}.png'.format(len(self.processed_images) + 1)
                save_path = os.path.join(self.save_directory, filename)

                # 确定保存格式
                ext = os.path.splitext(filename)[1].lower()
                if ext in ['.jpg', '.jpeg']:
                    image_format = 'JPEG'
                elif ext == '.png':
                    image_format = 'PNG'
                elif ext == '.gif':
                    image_format = 'GIF'
                elif ext == '.bmp':
                    image_format = 'BMP'
                elif ext in ['.tiff', '.tif']:
                    image_format = 'TIFF'
                else:
                    # 如果没有扩展名或扩展名未知，默认使用 PNG 格式
                    image_format = 'PNG'
                    filename += '.png'
                    save_path += '.png'

                # 保存图片并指定格式
                image_without_exif.save(save_path, format=image_format)
                self.processed_images.append(save_path)
            else:
                # 未勾选保存选项，保存到临时文件
                temp_dir = tempfile.gettempdir()
                if image_format == 'JPEG':
                    temp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.jpg', dir=temp_dir)
                elif image_format == 'PNG':
                    temp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.png', dir=temp_dir)
                else:
                    temp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.png', dir=temp_dir)
                    image_format = 'PNG'  # 统一使用 PNG 格式

                temp_file.close()
                image_without_exif.save(temp_file.name, format=image_format)
                self.temp_files.append(temp_file.name)
                self.processed_images.append(temp_file.name)
        except Exception as e:
            print(f'处理文件出错: {e}')

    def update_ui(self, message):
        self.label.setText(message)

    def copy_results(self):
        if self.processed_images:
            clipboard = QApplication.clipboard()
            mime_data = QMimeData()
            urls = [QUrl.fromLocalFile(path) for path in self.processed_images]
            mime_data.setUrls(urls)
            clipboard.setMimeData(mime_data)
            self.label.setText('已复制处理结果')
        else:
            self.label.setText('没有可复制的内容')

    def closeEvent(self, event):
        # 删除临时文件
        for temp_file in self.temp_files:
            try:
                os.remove(temp_file)
            except Exception as e:
                print(f'删除临时文件出错: {e}')
        event.accept()

if __name__ == '__main__':
    app = QApplication(sys.argv)
    ex = ImageProcessor()
    ex.show()
    sys.exit(app.exec_())
