#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
MTConnect Live Real-Time Web Dashboard
เว็บแอปพลิเคชันกราฟิกแบบโต้ตอบได้ด้วย Streamlit
ดึงข้อมูลจาก Official MTConnect Live Demo Agent (https://demo.mtconnect.org)
แสดงพิกัดเคลื่อนไหวจริงในรูปแบบกราฟเส้น, เกจ Spindle และระบบจำลองสัญญาณเตือนภัยสีแดงกะพริบ
"""

import time
import os
import json
import csv
import logging
import requests
import urllib3
import pandas as pd
import xml.etree.ElementTree as ET
from datetime import datetime
import streamlit as st

# ปิดระบบเตือนภัยเรื่อง SSL verification
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# กำหนดค่าเริ่มต้นของระบบ
CONFIG_FILE = "config.json"
DEFAULT_CONFIG = {
    "agent_url": "https://demo.mtconnect.org/current",
    "update_interval": 2.0,
    "device_name": "Mazak",
    "enable_csv_logging": True,
    "csv_log_path": "data/machine_data.csv",
    "enable_app_logging": True,
    "app_log_path": "logs/app.log"
}

def load_config():
    """
    โหลดไฟล์ตั้งค่า config.json ถ้าไม่มีให้สร้างค่าเริ่มต้นให้อัตโนมัติ
    """
    if not os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
                json.dump(DEFAULT_CONFIG, f, indent=4)
        except Exception:
            pass
        return DEFAULT_CONFIG
    
    try:
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            config = json.load(f)
            for k, v in DEFAULT_CONFIG.items():
                if k not in config:
                    config[k] = v
            return config
    except Exception:
        return DEFAULT_CONFIG

def setup_logging(config):
    """
    ตั้งค่าระบบการบันทึก Log ของแอปพลิเคชันลงไฟล์ logs/app.log
    """
    if not config.get("enable_app_logging", True):
        logging.basicConfig(level=logging.CRITICAL)
        return

    log_path = config.get("app_log_path", "logs/app.log")
    log_dir = os.path.dirname(log_path)
    if log_dir and not os.path.exists(log_dir):
        try:
            os.makedirs(log_dir, exist_ok=True)
        except Exception:
            pass
            
    try:
        logging.basicConfig(
            filename=log_path,
            level=logging.INFO,
            format='%(asctime)s [%(levelname)s] %(message)s',
            encoding='utf-8',
            force=True
        )
    except Exception:
        pass

def log_to_csv(config, data):
    """
    บันทึกสถานะพารามิเตอร์แกนและ spindle ลงไฟล์รายงานประวัติ CSV
    """
    if not config.get("enable_csv_logging", True) or data is None:
        return

    csv_path = config.get("csv_log_path", "data/machine_data.csv")
    csv_dir = os.path.dirname(csv_path)
    if csv_dir and not os.path.exists(csv_dir):
        try:
            os.makedirs(csv_dir, exist_ok=True)
        except Exception:
            pass

    axes = sorted(data.get("axes", {}).keys())
    headers = [
        "Timestamp", "API_Status", "Connection_Status", "Availability", 
        "Execution", "Controller_Mode", "Active_Program", "Tool_Number", 
        "Spindle_Speed_RPM", "Spindle_Load_Percent"
    ]
    for axis in axes:
        headers.append(f"Axis_{axis}_Pos")
        headers.append(f"Axis_{axis}_Load")

    file_exists = os.path.exists(csv_path)
    
    try:
        with open(csv_path, 'a', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            if not file_exists:
                writer.writerow(headers)
                
            row = [
                data.get("timestamp"),
                "Connected",
                data.get("connection_status"),
                data.get("availability"),
                data.get("execution"),
                data.get("controller_mode"),
                data.get("program"),
                data.get("tool_number"),
                data.get("spindle_speed", "UNAVAILABLE"),
                data.get("spindle_load", "UNAVAILABLE")
            ]
            
            for axis in axes:
                axis_info = data["axes"][axis]
                row.append(axis_info.get("pos", "UNAVAILABLE"))
                row.append(axis_info.get("load", "UNAVAILABLE"))
                
            writer.writerow(row)
    except Exception as e:
        logging.error(f"Failed to write CSV data: {str(e)}")

class MTConnectParser:
    """
    คลาสสำหรับดึงข้อมูลและวิเคราะห์ XML จาก MTConnect Live Agent
    """
    def __init__(self, url, device_name="Mazak"):
        self.url = url
        self.device_name = device_name
        self.last_status = "Unknown"
        self.latency = 0.0

    def fetch_data(self):
        """
        ดึงข้อมูล XML ล่าสุดและประมวลผล
        """
        start_time = time.time()
        try:
            response = requests.get(self.url, timeout=5, verify=False)
            self.latency = (time.time() - start_time) * 1000  # มิลลิวินาที
            if response.status_code == 200:
                self.last_status = "Connected"
                logging.info(f"Successfully fetched data from agent. Latency: {self.latency:.1f}ms")
                return self.parse_xml(response.content)
            else:
                self.last_status = f"HTTP Error {response.status_code}"
                logging.warning(f"Fetch failed with status code: {response.status_code}")
                return None
        except requests.exceptions.RequestException as e:
            self.last_status = "Offline / Connection Failed"
            self.latency = 0.0
            logging.error(f"Connection failed to {self.url}: {str(e)}")
            return None

    def parse_xml(self, xml_content):
        """
        วิเคราะห์โครงสร้าง XML และแยกข้อมูลของเครื่องจักรเป้าหมาย
        """
        try:
            root = ET.fromstring(xml_content)
        except ET.ParseError:
            self.last_status = "XML Parse Error"
            logging.error("Failed to parse response content as valid XML")
            return None

        ns = {}
        if '}' in root.tag:
            ns_url = root.tag.split('}')[0].strip('{')
            ns = {'m': ns_url}

        data = {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "agent_timestamp": "",
            "connection_status": "UNAVAILABLE",
            "availability": "UNAVAILABLE",
            "execution": "UNAVAILABLE",
            "program": "UNAVAILABLE",
            "tool_number": "UNAVAILABLE",
            "controller_mode": "UNAVAILABLE",
            "spindle_speed": "UNAVAILABLE",
            "spindle_load": "UNAVAILABLE",
            "axes": {},
            "alarms": []
        }

        header = root.find('.//m:Header', ns) if ns else root.find('.//Header')
        if header is not None:
            data["agent_timestamp"] = header.get("creationTime", "")

        streams = root.find('.//m:Streams', ns) if ns else root.find('.//Streams')
        if streams is None:
            return data

        target_stream = None
        for ds in streams.findall('.//m:DeviceStream', ns) if ns else streams.findall('.//DeviceStream'):
            if ds.get('name') == self.device_name:
                target_stream = ds
                break

        if target_stream is None:
            target_stream = streams.find('.//m:DeviceStream', ns) if ns else streams.find('.//DeviceStream')

        if target_stream is None:
            return data

        for component in target_stream.findall('.//m:ComponentStream', ns) if ns else target_stream.findall('.//ComponentStream'):
            comp_type = component.get('component')
            comp_name = component.get('name')

            if comp_type in ["Adapter", "Agent"]:
                events = component.find('m:Events', ns) if ns else component.find('Events')
                if events is not None:
                    conn_status = events.find('.//m:ConnectionStatus', ns) if ns else events.find('.//ConnectionStatus')
                    if conn_status is not None:
                        data["connection_status"] = conn_status.text or "UNAVAILABLE"
                    
                    avail = events.find('.//m:Availability', ns) if ns else events.find('.//Availability')
                    if avail is not None:
                        data["availability"] = avail.text or "UNAVAILABLE"

            elif comp_type == "Path":
                events = component.find('m:Events', ns) if ns else component.find('Events')
                if events is not None:
                    exec_item = events.find('.//m:Execution', ns) if ns else events.find('.//Execution')
                    if exec_item is not None:
                        data["execution"] = exec_item.text or "UNAVAILABLE"
                    
                    prog_item = events.find('.//m:Program', ns) if ns else events.find('.//Program')
                    if prog_item is not None:
                        data["program"] = prog_item.text or "UNAVAILABLE"
                    
                    tool_item = events.find('.//m:ToolNumber', ns) if ns else events.find('.//ToolNumber')
                    if tool_item is not None:
                        data["tool_number"] = tool_item.text or "UNAVAILABLE"

                    mode_item = events.find('.//m:ControllerMode', ns) if ns else events.find('.//ControllerMode')
                    if mode_item is not None:
                        data["controller_mode"] = mode_item.text or "UNAVAILABLE"

            elif comp_type in ["Linear", "Rotary"]:
                axis_name = comp_name

                if comp_type == "Rotary" and (axis_name == "C1" or axis_name == "C" or axis_name == "Spindle"):
                    samples = component.find('m:Samples', ns) if ns else component.find('Samples')
                    if samples is not None:
                        vel_item = samples.find('.//m:RotaryVelocity', ns) if ns else samples.find('.//RotaryVelocity')
                        if vel_item is not None:
                            data["spindle_speed"] = vel_item.text or "UNAVAILABLE"
                        
                        sload_item = samples.find('.//m:Load[@dataItemId="Sload"]', ns) if ns else samples.find('.//Load[@dataItemId="Sload"]')
                        if sload_item is None:
                            sload_item = samples.find('.//m:Load', ns) if ns else samples.find('.//Load')
                        if sload_item is not None:
                            data["spindle_load"] = sload_item.text or "UNAVAILABLE"
                    continue

                if axis_name not in data["axes"]:
                    data["axes"][axis_name] = {"pos": "UNAVAILABLE", "load": "UNAVAILABLE", "state": "UNAVAILABLE", "type": comp_type}

                samples = component.find('m:Samples', ns) if ns else component.find('Samples')
                events = component.find('m:Events', ns) if ns else component.find('Events')

                if samples is not None:
                    pos_tag = 'Angle' if comp_type == "Rotary" else 'Position'
                    pos_item = samples.find(f'.//m:{pos_tag}', ns) if ns else samples.find(f'.//{pos_tag}')
                    if pos_item is not None:
                        data["axes"][axis_name]["pos"] = pos_item.text or "UNAVAILABLE"
                    
                    load_item = samples.find('.//m:Load', ns) if ns else samples.find('.//Load')
                    if load_item is not None:
                        data["axes"][axis_name]["load"] = load_item.text or "UNAVAILABLE"

                if events is not None:
                    state_item = events.find('.//m:AxisState', ns) if ns else events.find('.//AxisState')
                    if state_item is not None:
                        data["axes"][axis_name]["state"] = state_item.text or "UNAVAILABLE"

            cond = component.find('m:Condition', ns) if ns else component.find('Condition')
            if cond is not None:
                for c_item in cond:
                    c_tag = c_item.tag.split('}')[-1]
                    if c_tag in ["Warning", "Fault"]:
                        data["alarms"].append({
                            "component": comp_type,
                            "name": comp_name or "System",
                            "severity": c_tag,
                            "type": c_item.get("type", "UNKNOWN"),
                            "id": c_item.get("dataItemId", ""),
                            "description": c_item.text or "Active Warning/Fault State"
                        })

        return data

# ฟังก์ชันช่วยเหลือในการวิเคราะห์ตัวเลข
def is_float(value):
    try:
        float(value)
        return True
    except (ValueError, TypeError):
        return False

# ==========================================
# STREAMLIT UI CODE
# ==========================================

st.set_page_config(
    page_title="MTConnect CNC Dashboard",
    page_icon="⚙️",
    layout="wide"
)

# โหลดคอนฟิก
config = load_config()
setup_logging(config)

# ตกแต่ง UI ด้วย Custom CSS
st.markdown("""
<style>
    .metric-card {
        background-color: #1e293b;
        border-radius: 10px;
        padding: 15px;
        border-left: 5px solid #3b82f6;
        margin-bottom: 10px;
    }
    .metric-value {
        font-size: 24px;
        font-weight: bold;
        color: #f1f5f9;
    }
    .metric-label {
        font-size: 14px;
        color: #94a3b8;
    }
</style>
""", unsafe_allow_html=True)

# ----------------- SIDEBAR -----------------
st.sidebar.title("⚙️ CNC Config Panel")
st.sidebar.markdown("ปรับค่าการเชื่อมต่อและเปิดทดสอบระบบเตือนภัย")

agent_url = st.sidebar.text_input("MTConnect Agent URL", config.get("agent_url", "https://demo.mtconnect.org/current"))
device_name = st.sidebar.text_input("Device Name Filter", config.get("device_name", "Mazak"))
update_interval = st.sidebar.slider("Polling Interval (seconds)", 1.0, 10.0, float(config.get("update_interval", 2.0)), 0.5)

st.sidebar.subheader("Logs & Export")
enable_csv = st.sidebar.checkbox("Enable CSV Logging", value=config.get("enable_csv_logging", True))
enable_log = st.sidebar.checkbox("Enable Diagnostics Logging", value=config.get("enable_app_logging", True))

# ปรับปรุงค่าในไฟล์ Config ทันทีที่เปลี่ยนบน UI
config["agent_url"] = agent_url
config["device_name"] = device_name
config["update_interval"] = update_interval
config["enable_csv_logging"] = enable_csv
config["enable_app_logging"] = enable_log

# สวิตช์จำลองสถานะสัญญาณเตือน (Simulation Test Mode)
st.sidebar.subheader("🚨 Demonstration Controls")
trigger_test_alarm = st.sidebar.toggle("Trigger Simulation Test Alarm", value=False)
pause_refresh = st.sidebar.checkbox("Pause Live Refresh", value=False)

# ----------------- DATA PROCESSING -----------------
parser = MTConnectParser(agent_url, device_name)
current_data = parser.fetch_data()

# จัดการกรณีดึงข้อมูลล้มเหลว
if current_data is None:
    current_data = {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "agent_timestamp": "-",
        "connection_status": "OFFLINE",
        "availability": "OFFLINE",
        "execution": "OFFLINE",
        "program": "OFFLINE",
        "tool_number": "OFFLINE",
        "controller_mode": "OFFLINE",
        "spindle_speed": "0",
        "spindle_load": "0",
        "axes": {},
        "alarms": [{"component": "System", "name": "Agent", "severity": "Warning", "type": "COMMUNICATION", "id": "net_err", "description": "Cannot connect to Live Agent Server"}]
    }
else:
    # เขียนบันทึกลง CSV ท้องถิ่น
    log_to_csv(config, current_data)

# หากเปิดโหมดจำลอง Alarm ให้ยัดข้อความเตือนภัยและสถานะเครื่อง
if trigger_test_alarm:
    current_data["execution"] = "CRITICAL_FAULT"
    current_data["controller_mode"] = "ALARM_STOP"
    current_data["alarms"] = [
        {
            "component": "Rotary",
            "name": "Spindle",
            "severity": "Fault",
            "type": "TEMPERATURE",
            "id": "spindle_overheat",
            "description": "[SIMULATED] Spindle motor overheat limit exceeded (> 95C)"
        },
        {
            "component": "Linear",
            "name": "X",
            "severity": "Warning",
            "type": "LOAD",
            "id": "x_load_warn",
            "description": "[SIMULATED] Axis X cutting force torque warning"
        }
    ]

# ----------------- REAL-TIME GRAPH HISTORY (SESSION STATE) -----------------
# เริ่มต้นจัดเก็บข้อมูลดิบสำหรับพล็อตประวัติกราฟเส้นขยับ
if 'history' not in st.session_state:
    st.session_state.history = pd.DataFrame(columns=['Time', 'Axis X', 'Axis Y', 'Axis Z', 'Spindle Speed'])

if not pause_refresh:
    # ดึงพิกัดแกนและ Spindle
    x_pos = current_data.get("axes", {}).get("X", {}).get("pos", 0.0)
    y_pos = current_data.get("axes", {}).get("Y", {}).get("pos", 0.0)
    z_pos = current_data.get("axes", {}).get("Z", {}).get("pos", 0.0)
    sp_speed = current_data.get("spindle_speed", 0.0)

    # แปลงเป็นตัวเลข
    x_val = float(x_pos) if is_float(x_pos) else 0.0
    y_val = float(y_pos) if is_float(y_pos) else 0.0
    z_val = float(z_pos) if is_float(z_pos) else 0.0
    sp_val = float(sp_speed) if is_float(sp_speed) else 0.0

    new_entry = pd.DataFrame([{
        'Time': datetime.now().strftime("%H:%M:%S"),
        'Axis X': x_val,
        'Axis Y': y_val,
        'Axis Z': z_val,
        'Spindle Speed': sp_val
    }])

    # ต่อท้ายข้อมูลและเก็บไว้สูงสุด 50 จุด
    st.session_state.history = pd.concat([st.session_state.history, new_entry], ignore_index=True)
    if len(st.session_state.history) > 50:
        st.session_state.history = st.session_state.history.iloc[-50:]

# ----------------- MAIN VIEW -----------------
st.title("⚙️ MTConnect CNC Real-Time Dashboard")

# 1. แบนเนอร์แสดงสถานะภัยคุกคาม / ALARM
alarms_list = current_data.get("alarms", [])
if alarms_list:
    st.error(f"🚨 [ALARM ACTIVE] Critical system warning(s) detected! Active Alarms: {len(alarms_list)}", icon="🚨")
else:
    st.success("🟢 [SYSTEM NORMAL] Connection stable. CNC machinery is operating safely.", icon="🟢")

# 2. แผงแสดงข้อมูลทั่วไป (System Overview Metrics)
col1, col2, col3, col4, col5 = st.columns(5)
with col1:
    conn_status = "Connected" if parser.last_status == "Connected" else "Disconnected"
    st.metric(label="API Status", value=conn_status, delta=f"{parser.latency:.1f} ms" if parser.last_status == "Connected" else None)
with col2:
    st.metric(label="Execution Mode", value=current_data.get("execution"))
with col3:
    st.metric(label="Controller Mode", value=current_data.get("controller_mode"))
with col4:
    st.metric(label="Active Program", value=current_data.get("program"))
with col5:
    st.metric(label="Active Tool", value=f"T{current_data.get('tool_number')}")

st.divider()

# จัดหน้าจอแบบ 2 คอลัมน์ซ้ายขวา
main_col_left, main_col_right = st.columns([1, 1])

with main_col_left:
    # 3. ข้อมูลตำแหน่งของแกนขับเรียลไทม์ (Live Charts)
    st.subheader("📈 CNC Axes Trajectory (Real-time)")
    if not st.session_state.history.empty:
        # พล็อตกราฟแกน X, Y, Z
        st.line_chart(
            st.session_state.history,
            x="Time",
            y=["Axis X", "Axis Y", "Axis Z"],
            use_container_width=True
        )
    
    st.subheader("📊 Spindle RPM Profile")
    if not st.session_state.history.empty:
        # พล็อตกราฟ Spindle Speed
        st.line_chart(
            st.session_state.history,
            x="Time",
            y="Spindle Speed",
            color="#ec4899",
            use_container_width=True
        )

with main_col_right:
    # 4. ตารางพารามิเตอร์ Spindle & แกนเลื่อนแบบละเอียด (Detailed Table)
    st.subheader("📋 Drive & Spindle Details")
    
    # ดึงและแสดงค่า Spindle
    sp_speed_txt = f"{float(sp_speed):.0f} RPM" if is_float(sp_speed) else "UNAVAILABLE"
    sp_load = current_data.get("spindle_load", 0.0)
    sp_load_txt = f"{float(sp_load):.1f} %" if is_float(sp_load) else "UNAVAILABLE"
    
    st.markdown(f"**Main Spindle Status:** {sp_speed_txt} | **Load:** {sp_load_txt}")
    if is_float(sp_load):
        # แถบแสดงพลังงานเปอร์เซ็นต์โหลด
        st.progress(min(max(float(sp_load) / 100.0, 0.0), 1.0))
        
    st.write("")
    
    # ประกอบโครงสร้างตารางแสดงพิกัดและโหลดของแต่ละแกน
    axes_data = []
    sorted_axes = sorted(current_data.get("axes", {}).keys())
    for axis in sorted_axes:
        info = current_data["axes"][axis]
        pos = info.get("pos")
        unit = "deg" if info.get("type") == "Rotary" else "mm"
        pos_txt = f"{float(pos):.4f} {unit}" if is_float(pos) else pos
        
        load = info.get("load")
        load_txt = f"{float(load):.1f} %" if is_float(load) else load
        
        axes_data.append({
            "Axis Component": f"Axis {axis}",
            "Position/Angle": pos_txt,
            "Axis Load": load_txt,
            "State Status": info.get("state")
        })
        
    if axes_data:
        st.table(pd.DataFrame(axes_data))
    else:
        st.info("No axis drive components found in stream.")
        
    st.write("")

    # 5. รายการการแจ้งเตือนสัญญาณเตือน (Active Alarms Panel)
    st.subheader("⚠️ Active System Alarms & Conditions")
    if alarms_list:
        for alarm in alarms_list:
            severity = alarm.get("severity")
            header_color = "red" if severity == "Fault" else "orange"
            st.markdown(f"""
            <div style="background-color:rgba(239, 68, 68, 0.1); border-left:5px solid {header_color}; padding:10px; border-radius:5px; margin-bottom:8px;">
                <span style="color:{header_color}; font-weight:bold;">[{severity.upper()}]</span> 
                <b>Component:</b> {alarm.get('component')} ({alarm.get('name')})<br/>
                <b>Description:</b> {alarm.get('description')} <span style="font-size:12px; color:#64748b;">(ID: {alarm.get('id')})</span>
            </div>
            """, unsafe_allow_html=True)
    else:
        st.markdown("""
        <div style="background-color:rgba(34, 197, 94, 0.1); border-left:5px solid #22c55e; padding:10px; border-radius:5px;">
            <span style="color:#22c55e; font-weight:bold;">[OK]</span> No active alarms. Factory machinery status is Normal.
        </div>
        """, unsafe_allow_html=True)

# ----------------- FOOTER & AUTO-REFRESH -----------------
st.divider()
st.caption(f"System Timestamp: {current_data.get('timestamp')} | Agent Timestamp: {current_data.get('agent_timestamp')} | Refresh Interval: {update_interval}s")

if not pause_refresh:
    # หน่วงเวลารอการดึงข้อมูลรอบใหม่
    time.sleep(update_interval)
    st.rerun()
