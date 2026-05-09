import sys
import serial
import serial.tools.list_ports
import time
import csv
from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                             QHBoxLayout, QPushButton, QLabel, QSpinBox, QDoubleSpinBox, 
                             QMessageBox, QComboBox, QFileDialog)
from PyQt5.QtCore import QThread, pyqtSignal, Qt
import pyqtgraph as pg
import pyqtgraph.exporters

# --- PHYSICAL CONSTANTS ---
MAX_PHYSICAL_VOLTAGE = 1600.0
MAX_DAC_VALUE = 4095.0
VOLTAGE_DIVIDER = 500.0
CURRENT_SHUNT_OHMS = 39000.0
ADC_LSB = 125e-6

class SerialListener(QThread):
    data_received = pyqtSignal(float, float) 
    finished_sig = pyqtSignal()
    error_sig = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.ser = None
        self.running = True

    def connect_port(self, port_name):
        try:
            if self.ser and self.ser.is_open:
                self.ser.close()
            self.ser = serial.Serial(port_name, 115200, timeout=0.1)
            self.ser.setDTR(True)
            return True
        except Exception as e:
            self.error_sig.emit(str(e))
            return False

    def run(self):
        while self.running:
            if self.ser and self.ser.is_open:
                try:
                    if self.ser.in_waiting > 0:
                        line = self.ser.readline().decode('ascii', errors='ignore').strip()
                        if line.startswith("D:"):
                            parts = line.split(';')
                            raw_a0 = int(parts[1].split(':')[1])
                            raw_a1 = int(parts[2].split(':')[1])
                            v = abs(raw_a0) * ADC_LSB * VOLTAGE_DIVIDER
                            i = abs(raw_a1) * ADC_LSB / CURRENT_SHUNT_OHMS
                            self.data_received.emit(v, i)
                        elif "DONE" in line:
                            self.finished_sig.emit()
                except: pass
            time.sleep(0.01)

    def send_cmd(self, start_v, stop_v, step_v, delay):
        if self.ser and self.ser.is_open:
            d_start = int((start_v / MAX_PHYSICAL_VOLTAGE) * MAX_DAC_VALUE)
            d_stop = int((stop_v / MAX_PHYSICAL_VOLTAGE) * MAX_DAC_VALUE)
            d_step = int((step_v / MAX_PHYSICAL_VOLTAGE) * MAX_DAC_VALUE)
            self.ser.reset_input_buffer()
            cmd = f"S:{max(0,min(4095,d_start)):04d};E:{max(0,min(4095,d_stop)):04d};T:{max(1,min(4095,d_step)):04d};D:{int(delay):04d};"
            self.ser.write(cmd.encode())

    def stop_hw(self):
        if self.ser and self.ser.is_open:
            self.ser.write(b"0")

class FEmaster(QMainWindow):
    def __init__(self):
        super().__init__()
        self.v_data, self.i_data = [], []
        self.listener = SerialListener()
        self.initUI()
        self.scan_ports()
        self.listener.start()

    def initUI(self):
        self.setWindowTitle("FEmaster_v1.0")
        self.setMinimumSize(1100, 850)
        self.setStyleSheet("QMainWindow { background-color: #f0f2f5; } QLabel { font-weight: bold; }")

        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QHBoxLayout(central)

        # CONTROL PANEL
        controls = QVBoxLayout()
        controls.setContentsMargins(15, 15, 15, 15)
        
        # Connection Block
        controls.addWidget(QLabel("COM Port:"))
        port_layout = QHBoxLayout()
        self.cb_ports = QComboBox(); self.cb_ports.setFixedHeight(35)
        port_layout.addWidget(self.cb_ports, 3)
        self.btn_refresh = QPushButton("Refresh"); self.btn_refresh.clicked.connect(self.scan_ports); self.btn_refresh.setFixedHeight(35)
        port_layout.addWidget(self.btn_refresh, 1)
        controls.addLayout(port_layout)

        self.btn_connect = QPushButton("Connect Device")
        self.btn_connect.clicked.connect(self.handle_connect); self.btn_connect.setFixedHeight(40)
        self.btn_connect.setStyleSheet("background-color: #3498db; color: white;")
        controls.addWidget(self.btn_connect)
        
        controls.addWidget(QLabel("-" * 20))

        # Measurement Settings
        self.spn_start = self.add_ctrl(controls, "Start Voltage (V):", 0, 1600, 0)
        self.spn_stop = self.add_ctrl(controls, "Stop Voltage (V):", 0, 1600, 1000)
        
        controls.addWidget(QLabel("Points Count:"))
        self.spn_points = QSpinBox(); self.spn_points.setRange(2, 5000); self.spn_points.setValue(20); self.spn_points.setFixedHeight(40)
        controls.addWidget(self.spn_points)

        controls.addWidget(QLabel("Step Voltage (V):"))
        self.spn_step = QDoubleSpinBox(); self.spn_step.setRange(0.1, 1600.0); self.spn_step.setFixedHeight(40)
        controls.addWidget(self.spn_step)

        self.spn_delay = self.add_ctrl(controls, "Step Delay (ms):", 10, 10000, 200)
        self.spn_max_i = self.add_ctrl(controls, "Current Limit (µA):", 0, 10000, 100)

        controls.addWidget(QLabel("-" * 20))
        
        # Export Block
        controls.addWidget(QLabel("Export Options:"))
        self.btn_save_csv = QPushButton("Export Data (CSV)")
        self.btn_save_csv.clicked.connect(self.export_csv)
        controls.addWidget(self.btn_save_csv)

        self.btn_save_img = QPushButton("Save Plot (PNG)")
        self.btn_save_img.clicked.connect(self.export_image)
        controls.addWidget(self.btn_save_img)

        controls.addStretch()
        
        self.btn_start = QPushButton("START SCAN"); self.btn_start.setFixedHeight(60); self.btn_start.setStyleSheet("background: #27ae60; color: white; font-size: 16px;")
        self.btn_start.clicked.connect(self.start_scan)
        controls.addWidget(self.btn_start)

        self.btn_stop = QPushButton("EMERGENCY STOP"); self.btn_stop.setFixedHeight(60); self.btn_stop.setStyleSheet("background: #c0392b; color: white; font-size: 16px;")
        self.btn_stop.clicked.connect(self.stop_scan)
        controls.addWidget(self.btn_stop)

        main_layout.addLayout(controls, 1)

        # PLOT WIDGET
        self.graph = pg.PlotWidget(title="I-V Characteristic")
        self.graph.setBackground('w')
        self.graph.setLabel('left', 'Current', units='A')
        self.graph.setLabel('bottom', 'Voltage', units='V')
        
        # Grid settings
        self.graph.showGrid(x=True, y=True, alpha=0.3)
        
        self.plot_item = self.graph.plot(pen=pg.mkPen('b', width=2), symbol='o', symbolSize=6, symbolBrush='b')
        main_layout.addWidget(self.graph, 3)

        self.listener.data_received.connect(self.on_data)
        self.listener.finished_sig.connect(self.on_done)
        self.listener.error_sig.connect(lambda e: QMessageBox.warning(self, "Connection Error", e))
        
        self.update_math()
        self.spn_points.valueChanged.connect(self.update_math)
        self.spn_step.valueChanged.connect(self.update_pts)

    def add_ctrl(self, layout, text, min_v, max_v, def_v):
        layout.addWidget(QLabel(text))
        if "Limit" in text:
            s = QDoubleSpinBox()
        else:
            s = QSpinBox()
        s.setRange(min_v, max_v); s.setValue(def_v); s.setFixedHeight(40)
        layout.addWidget(s)
        return s

    def scan_ports(self):
        self.cb_ports.clear()
        ports = [p.device for p in serial.tools.list_ports.comports()]
        self.cb_ports.addItems(ports)

    def handle_connect(self):
        port = self.cb_ports.currentText()
        if port and self.listener.connect_port(port):
            self.btn_connect.setText(f"Connected to {port}")
            self.btn_connect.setStyleSheet("background: #2ecc71; color: white;")

    def update_math(self):
        self.spn_step.blockSignals(True)
        span = self.spn_stop.value() - self.spn_start.value()
        pts = self.spn_points.value()
        if pts > 1: self.spn_step.setValue(span / (pts - 1))
        self.spn_step.blockSignals(False)

    def update_pts(self):
        self.spn_points.blockSignals(True)
        span = self.spn_stop.value() - self.spn_start.value()
        step = self.spn_step.value()
        if step > 0: self.spn_points.setValue(int(span / step) + 1)
        self.spn_points.blockSignals(False)

    def start_scan(self):
        if not self.listener.ser or not self.listener.ser.is_open:
            QMessageBox.warning(self, "Warning", "Please connect the device first!")
            return
        self.v_data, self.i_data = [], []
        self.plot_item.setData([], [])
        self.btn_start.setEnabled(False)
        self.btn_start.setText("RUNNING...")
        self.listener.send_cmd(self.spn_start.value(), self.spn_stop.value(), 
                              self.spn_step.value(), self.spn_delay.value())

    def on_data(self, v, i):
        limit = self.spn_max_i.value() / 1e6
        if i > limit:
            self.stop_scan()
            QMessageBox.critical(self, "OVERCURRENT PROTECT", f"Current limit exceeded!\n{i*1e6:.2f} µA > {self.spn_max_i.value()} µA")
            return
        self.v_data.append(v)
        self.i_data.append(i)
        self.plot_item.setData(self.v_data, self.i_data)

    def stop_scan(self):
        self.listener.stop_hw()
        self.on_done()

    def on_done(self):
        self.btn_start.setEnabled(True)
        self.btn_start.setText("START SCAN")

    def export_csv(self):
        if not self.v_data: return
        path, _ = QFileDialog.getSaveFileName(self, "Save Data Points", "", "CSV Files (*.csv)")
        if path:
            with open(path, 'w', newline='') as f:
                writer = csv.writer(f, delimiter=';')
                writer.writerow(['Voltage (V)', 'Current (A)'])
                for v, i in zip(self.v_data, self.i_data):
                    writer.writerow([f"{v:.4f}", f"{i:.10f}"])

    def export_image(self):
        if not self.v_data: return
        path, _ = QFileDialog.getSaveFileName(self, "Save Plot Image", "", "PNG Files (*.png)")
        if path:
            exporter = pg.exporters.ImageExporter(self.graph.plotItem)
            exporter.export(path)

if __name__ == "__main__":
    app = QApplication(sys.argv)
    win = FEmaster(); win.show()
    sys.exit(app.exec_())