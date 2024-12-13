import sys
import os
import tempfile
import shutil
import piexif
import requests
from dataclasses import dataclass
from typing import Optional, List, Tuple
from PyQt5.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QLabel, QPushButton,
    QFileDialog, QCheckBox, QLineEdit, QMessageBox, QHBoxLayout
)
from PyQt5.QtCore import (
    Qt, QMimeData, pyqtSignal, QObject, QByteArray, QBuffer,
    QIODevice, QUrl, QSettings, QThreadPool, QRunnable
)
from PyQt5.QtGui import QPixmap, QImage, QIcon
from PIL import Image
import io

@dataclass
class ImageTask:
    """图片处理任务"""
    def __init__(self, index: int, data_type: str, data: str, save_path: str = None, original_name: str = None):
        self.index = index
        self.data_type = data_type
        self.data = data
        self.save_path = save_path
        self.original_name = original_name
        self.result = None
        self.error = None

class ImageWorkerSignals(QObject):
    """工作线程信号"""
    finished = pyqtSignal(ImageTask)  # 任务完成信号
    progress = pyqtSignal(int, int)   # 进度信号
    error = pyqtSignal(str, str)      # 错误信号 (错误消息, 详细信息)

class ImageWorker(QRunnable):
    """图片处理工作线程"""
    def __init__(self, task: ImageTask):
        super().__init__()
        self.task = task
        self.signals = ImageWorkerSignals()

    def run(self):
        try:
            if self.task.data_type == 'file':
                success = remove_exif(self.task.data, self.task.save_path)
            else:
                # 对于非文件类型，先保存为临时文件
                temp_buffer = tempfile.NamedTemporaryFile(delete=False)
                if self.task.data_type == 'image':
                    buffer = QBuffer()
                    buffer.open(QIODevice.WriteOnly)
                    self.task.data.save(buffer, "PNG")
                    temp_buffer.write(buffer.data())
                else:  # URL
                    response = requests.get(self.task.data)
                    temp_buffer.write(response.content)
                temp_buffer.close()
                
                success = remove_exif(temp_buffer.name, self.task.save_path)
                os.unlink(temp_buffer.name)
            
            if not success:
                raise Exception("无法处理文件元数据")
            self.task.result = success
            
        except Exception as e:
            self.task.result = False
            self.task.error = str(e)
            # 发送错误信号，包含文件名和详细错误信息
            error_msg = f"处理文件 {self.task.original_name} 时出错"
            self.signals.error.emit(error_msg, str(e))
        
        finally:
            self.signals.finished.emit(self.task)

class ImageProcessor(QWidget):
    def __init__(self):
        super().__init__()
        self.settings = QSettings('CookSleep', 'ImageMetadataRemover')
        self.save_directory = self.settings.value('save_directory', '', type=str)
        self.save_checkbox_state = self.settings.value('save_checkbox_state', False, type=bool)
        self.always_on_top_state = self.settings.value('always_on_top_state', False, type=bool)
        
        # 初始化线程池
        self.threadpool = QThreadPool()
        self.threadpool.setMaxThreadCount(3)  # 最多3个线程
        
        self.results = []  # 存储处理结果，按原始顺序
        self.completed_count = 0  # 已完成的任务数
        self.total_tasks = 0  # 总任务数
        self.errors = []  # 错误信息列表
        
        self.initUI()
        self.temp_files = []
        
        # 设置复选框状态
        self.save_checkbox.setChecked(self.save_checkbox_state)
        
        # 根据复选框状态显示或隐藏目录输入框
        if self.save_checkbox.isChecked():
            if self.save_directory:
                self.dir_input.setText(self.save_directory)
                self.dir_input.show()
            else:
                self.dir_input.hide()
        else:
            self.dir_input.hide()
        
        # 设置"窗口置顶"复选框状态
        self.always_on_top_checkbox.setChecked(self.always_on_top_state)
        self.toggle_always_on_top(Qt.Checked if self.always_on_top_state else Qt.Unchecked)
        
        # 检查保存目录状态并设置提示信息
        self.update_directory_status()

    def initUI(self):
        self.setWindowTitle('图片元数据消除器')
        icon_path = resource_path("icon.ico")
        self.setWindowIcon(QIcon(icon_path))  # 设置窗口图标
        self.setAcceptDrops(True)
        self.resize(400, 300)

        self.layout = QVBoxLayout()

        self.label = QLabel('将图片拖拽到此窗口')
        self.label.setAlignment(Qt.AlignCenter)
        self.layout.addWidget(self.label)

        self.save_checkbox = QCheckBox('保存处理后图片到指定目录')
        self.save_checkbox.stateChanged.connect(self.on_save_checkbox_changed)
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

    def handle_worker_finished(self, task: ImageTask):
        """处理工作线程完成的任务"""
        self.completed_count += 1
        if task.result:
            self.results[task.index] = (task.save_path, task.original_name)
        
        # 更新进度
        self.update_progress(self.completed_count, self.total_tasks)
        
        # 检查是否所有任务都完成
        if self.completed_count == self.total_tasks:
            success_count = sum(1 for r in self.results if r is not None)
            status_text = []
            
            # 添加主要状态信息
            if success_count == 0:
                status_text.append('处理失败')
                self.label.setStyleSheet('color: red;')
                self.show_error_dialog()
            elif success_count == self.total_tasks:
                status_text.append('处理完成')
                self.label.setStyleSheet('color: green;')
            else:
                status_text.append(f'部分失败 ({success_count}/{self.total_tasks} 成功)')
                self.label.setStyleSheet('color: orange;')
                self.show_error_dialog()
            
            # 如果勾选了保存且目录不存在，添加提示信息
            if self.save_checkbox.isChecked() and self.save_directory and not os.path.exists(self.save_directory):
                # 如果是失败或部分失败的情况，添加"并且"
                if success_count < self.total_tasks:
                    status_text.extend(['', '并且'])
                status_text.extend(['', '<span style="color: red;">所选保存目录不存在</span>'])
            
            # 更新状态文本，使用HTML格式支持不同颜色
            self.label.setText('<br>'.join(status_text))
            self.label.setTextFormat(Qt.TextFormat.RichText)

    def handle_worker_error(self, error_msg: str, error_detail: str):
        """处理工作线程错误"""
        self.errors.append((error_msg, error_detail))

    def show_error_dialog(self):
        """显示错误信息对话框"""
        if self.errors:
            error_text = "处理过程中发生以下错误：\n\n"
            for title, detail in self.errors:
                error_text += f"{title}\n详细信息：{detail}\n\n"
            
            msg_box = QMessageBox(self)
            msg_box.setIcon(QMessageBox.Warning)
            msg_box.setWindowTitle("处理错误")
            msg_box.setText(error_text.strip())
            msg_box.setStandardButtons(QMessageBox.Ok)
            msg_box.exec_()
            
            # 清空错误列表
            self.errors.clear()

    def toggle_always_on_top(self, state):
        self.settings.setValue('always_on_top_state', state == Qt.Checked)  # 保存“窗口置顶”复选框状态
        if state == Qt.Checked:
            self.setWindowFlags(self.windowFlags() | Qt.WindowStaysOnTopHint)
        else:
            self.setWindowFlags(self.windowFlags() & ~Qt.WindowStaysOnTopHint)
        self.show()

    def on_save_checkbox_changed(self, state):
        """处理保存选项变化"""
        self.settings.setValue('save_checkbox_state', state == Qt.Checked)
        if state == Qt.Checked:
            # 如果从未选择过目录，弹出选择框
            if not self.save_directory:
                dir_path = QFileDialog.getExistingDirectory(self, '选择保存目录')
                if dir_path:
                    self.save_directory = dir_path
                    self.dir_input.setText(self.save_directory)
                    self.settings.setValue('save_directory', self.save_directory)
                    self.dir_input.show()
                else:
                    self.save_checkbox.setChecked(False)
                    return
            else:
                # 如果之前选择过目录，直接显示（无论目录是否存在）
                self.dir_input.setText(self.save_directory)
                self.dir_input.show()
        else:
            # 取消勾选时隐藏目录输入框，但保留目录设置
            self.dir_input.hide()
            
        # 更新提示信息
        self.update_directory_status()

    def update_directory_status(self):
        """更新保存目录状态和提示信息"""
        if not self.save_checkbox.isChecked():
            # 未勾选保存时，显示默认提示
            self.label.setText('将图片拖拽到此窗口')
            self.label.setStyleSheet('')
            return

        # 已勾选保存的情况
        if not self.save_directory:
            # 从未选择过目录
            self.label.setText('请选择保存目录')
            self.label.setStyleSheet('color: orange;')
        elif not os.path.exists(self.save_directory):
            # 目录不存在时
            self.label.setText('所选保存目录不存在')
            self.label.setStyleSheet('color: red;')
            self.dir_input.setStyleSheet('color: red;')
        else:
            # 目录存在时
            self.label.setText('将图片拖拽到此窗口')
            self.label.setStyleSheet('')
            self.dir_input.setStyleSheet('')

    def check_and_set_save_dir(self):
        """检查并设置保存目录"""
        if self.save_directory:
            self.dir_input.setText(self.save_directory)
            self.dir_input.show()
            self.update_directory_status()
            return True
            
        # 从未选择过目录时，请求选择
        dir_path = QFileDialog.getExistingDirectory(self, '选择保存目录')
        if dir_path:
            self.save_directory = dir_path
            self.dir_input.setText(self.save_directory)
            self.settings.setValue('save_directory', self.save_directory)
            self.dir_input.show()
            self.update_directory_status()
            return True
        else:
            self.save_checkbox.setChecked(False)
            return False

    def choose_directory(self, event=None):
        directory = QFileDialog.getExistingDirectory(self, '选择保存目录', self.save_directory or os.getcwd())
        if directory:
            self.save_directory = directory
            self.dir_input.setText(self.save_directory)
            self.settings.setValue('save_directory', self.save_directory)
            self.update_directory_status()

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
            self.label.setStyleSheet('color: red;')
            return

        if self.image_data_list:
            self.process_images()
        else:
            self.label.setText('不支持的文件类型')
            self.label.setStyleSheet('color: red;')

    def update_progress(self, current, total):
        self.label.setText(f'正在处理... ({current}/{total})')
        self.label.setStyleSheet('color: blue;')

    def copy_results(self):
        """复制处理结果到剪贴板"""
        # 只复制成功的结果
        successful_results = [(path, name) for result in self.results if result is not None for path, name in [result]]
        if successful_results:
            clipboard = QApplication.clipboard()
            mime_data = QMimeData()
            urls = []
            new_temp_files = []  # 新的临时文件列表

            # 如果启用了保存且保存目录存在，直接使用保存目录中的文件
            if self.save_checkbox.isChecked() and self.save_directory and os.path.exists(self.save_directory):
                for _, original_name in successful_results:
                    saved_path = os.path.join(self.save_directory, original_name)
                    if os.path.exists(saved_path):
                        urls.append(QUrl.fromLocalFile(saved_path))
            # 否则创建临时文件，使用原始文件名
            else:
                for path, original_name in successful_results:
                    if os.path.exists(path):
                        # 创建一个临时文件，使用原始文件名
                        temp_dir = os.path.dirname(path)
                        new_path = os.path.join(temp_dir, original_name)
                        try:
                            # 如果目标文件已存在，先删除
                            if os.path.exists(new_path):
                                os.remove(new_path)
                            # 复制文件并使用原始文件名
                            shutil.copy2(path, new_path)
                            # 添加新路径到临时文件列表
                            new_temp_files.append(new_path)
                            urls.append(QUrl.fromLocalFile(new_path))
                        except Exception as e:
                            print(f"复制文件时出错: {e}")
                            # 如果复制失败，使用原始路径
                            urls.append(QUrl.fromLocalFile(path))

            # 更新临时文件列表
            self.temp_files.extend(new_temp_files)

            if urls:  # 只有在有成功复制的文件时才设置剪贴板
                mime_data.setUrls(urls)
                clipboard.setMimeData(mime_data)
                self.label.setText('已复制处理结果')
                self.label.setStyleSheet('color: green;')
            else:
                self.label.setText('复制失败')
                self.label.setStyleSheet('color: red;')
        else:
            self.label.setText('没有可复制的结果')
            self.label.setStyleSheet('color: red;')

    def closeEvent(self, event):
        """关闭窗口时清理临时文件"""
        # 保存设置
        self.settings.setValue('save_directory', self.save_directory)
        self.settings.setValue('save_checkbox_state', self.save_checkbox.isChecked())
        self.settings.setValue('always_on_top_state', self.always_on_top_checkbox.isChecked())
        
        # 清理临时文件
        for temp_file in self.temp_files:
            try:
                if os.path.exists(temp_file):
                    os.remove(temp_file)
            except Exception as e:
                print(f"清理临时文件失败: {e}")
        
        # 清空临时文件列表
        self.temp_files.clear()
        event.accept()

    def process_images(self):
        """处理所有图片"""
        if not self.image_data_list:
            return

        # 重置处理状态
        self.completed_count = 0
        self.total_tasks = len(self.image_data_list)
        self.results = [None] * self.total_tasks
        self.errors = []  # 清空错误列表

        # 检查是否需要保存到目录
        save_to_dir = self.save_checkbox.isChecked() and self.save_directory and os.path.exists(self.save_directory)

        # 创建并启动工作线程
        for i, (data_type, data) in enumerate(self.image_data_list):
            # 准备文件名
            if data_type == 'file':
                original_name = os.path.basename(data)
            elif data_type == 'url':
                original_name = os.path.basename(data.split('?')[0])  # 移除URL参数
            else:  # data_type == 'image'
                original_name = f'clipboard_{i + 1}.png'

            # 准备保存路径
            if save_to_dir:
                save_path = os.path.join(self.save_directory, original_name)
            else:
                # 创建临时文件
                suffix = '.png' if data_type == 'image' else os.path.splitext(data)[1] if data_type == 'file' else '.jpg'
                with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                    save_path = tmp.name
                    self.temp_files.append(tmp.name)

            # 创建任务
            task = ImageTask(i, data_type, data, save_path, original_name)
            worker = ImageWorker(task)
            worker.signals.finished.connect(self.handle_worker_finished)
            worker.signals.error.connect(self.handle_worker_error)
            self.threadpool.start(worker)

        self.update_progress(0, self.total_tasks)

def remove_exif(src_path, dst_path):
    """直接移除图片的元数据，不重新编码图片内容"""
    try:
        # 先尝试用 PIL 检查图片格式
        with Image.open(src_path) as img:
            format = img.format.upper()
            
            # 对于 JPEG/WEBP 使用 piexif
            if format in ['JPEG', 'WEBP']:
                try:
                    piexif.remove(src_path, dst_path)
                    return True
                except Exception as e:
                    raise Exception(f"处理 {format} 格式时出错: {str(e)}")
            
            # 对于 PNG 格式
            elif format == 'PNG':
                try:
                    with open(src_path, 'rb') as src:
                        data = src.read()
                        if data.startswith(b'\x89PNG\r\n\x1a\n'):
                            pos = 8
                            chunks = []
                            while pos < len(data):
                                length = int.from_bytes(data[pos:pos+4], 'big')
                                chunk_type = data[pos+4:pos+8]
                                if chunk_type not in [b'tEXt', b'iTXt', b'zTXt']:
                                    chunks.append(data[pos:pos+length+12])
                                pos += length + 12
                            
                            with open(dst_path, 'wb') as dst:
                                dst.write(data[:8])
                                for chunk in chunks:
                                    dst.write(chunk)
                            return True
                except Exception as e:
                    raise Exception(f"处理 PNG 格式时出错: {str(e)}")
            
            # 对于其他格式，复制图像数据到新图片
            try:
                new_img = Image.new(img.mode, img.size)
                new_img.putdata(list(img.getdata()))
                new_img.save(dst_path, format=format)
                return True
            except Exception as e:
                raise Exception(f"处理 {format} 格式图像时出错: {str(e)}")
                
    except Exception as e:
        raise Exception(str(e))

def resource_path(relative_path):
    """获取资源文件的绝对路径，支持 PyInstaller"""
    try:
        # PyInstaller 创建的临时文件夹路径
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)

if __name__ == '__main__':
    app = QApplication(sys.argv)
    ex = ImageProcessor()
    ex.show()
    sys.exit(app.exec_())
