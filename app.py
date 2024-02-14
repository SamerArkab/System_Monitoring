from flask import Flask
from flask_migrate import Migrate
from urls import configure_routes
from models import db, Memory, Cpu, Disk, ActiveProcesses
import os
import psutil 
from datetime import datetime
import threading
import time
import shared
import paramiko


app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'default_fallback_secret_key')
app.config['SQLALCHEMY_DATABASE_URI'] = "sqlite:///site.db"

db.init_app(app)
migrate = Migrate(app, db)

configure_routes(app)

DEFAULT_HOSTNAME = 'localhost'
hostname_changed_event = threading.Event()
shared.current_hostname = DEFAULT_HOSTNAME

def bytes_to_gb(bytes):
    return bytes / (1024.0 ** 3)

def kb_to_gb(kb):
     return round(float(kb) / 1024 / 1024,2)

def collect_remote_system_info(ssh_client):
    try:
        stdin, stdout, stderr = ssh_client.exec_command("top -bn 1 | grep Cpu")
        output = stdout.read().decode("utf-8")
        parts = output.split(',')
        times_user = round(float(parts[0].split()[1]),2)
        times_system = round(float(parts[1].split()[0]),2)
        times_idle = round(float(parts[3].split()[0]),2)
        usage_percent = 100.0 - times_idle
        cpu_data = Cpu(times_user=times_user,
                           times_system=times_system,
                           times_idle=times_idle,
                            usage_percent=usage_percent,
                            host_ip=shared.current_hostname)
        stdin, stdout, stderr = ssh_client.exec_command("top -bn 1 | grep Mem")
        output = stdout.read().decode("utf-8")
        data_line = output.split('\n')[0]
        parts = output.split(',')
        mem_used=round(float(parts[2].split()[0]),2)
        free_mem=round(float(parts[1].split()[0]),2)
        total_mem=round(float(parts[0].split()[3]),2)
        usage_percent = (mem_used / total_mem) * 100
        stdin, stdout, stderr = ssh_client.exec_command('cat /proc/meminfo | grep -E "Active:|Inactive:"')
        output = stdout.read().decode("utf-8")
        active_mem_kb = output.split('\n')[0].split(':')[1]
        active_mem = active_mem_kb.split()[0]
        Inactive_mem_kb = output.split('\n')[1].split(':')[1]
        Inactive_mem = Inactive_mem_kb.split()[0]
        memory_data = Memory(used=round(bytes_to_gb(mem_used),2), 
                                 active=kb_to_gb(active_mem),
                                 inactive=kb_to_gb(Inactive_mem),
                                usage_percent=round(usage_percent,2),
                                host_ip=shared.current_hostname)
        stdin, stdout, stderr = ssh_client.exec_command('df -h /')
        output = stdout.read().decode("utf-8")
        data_line = output.split('\n')[1]
        parts=data_line.split()
        disk_size=parts[1]
        disk_used=parts[2]
        disk_avil=parts[3]
        disk_percent=parts[4]
        shared.total_space=disk_size
        disk_data = Disk(used=round(float(disk_used[:-1]),2),
                             free=round(float(disk_avil[:-1]) ,2) ,
                            usage_percent=disk_percent[:1],
                            host_ip=shared.current_hostname)
        
        
        
        
        db.session.add(cpu_data)
        db.session.add(disk_data)
        db.session.add(memory_data)
        db.session.commit()
        

        # # Execute commands to collect system info remotely
        # stdin, stdout, stderr = ssh_client.exec_command("free -m")  # Example command to get memory info
        # mem_info = stdout.read().decode("utf-8")
        # stdin, stdout, stderr = ssh_client.exec_command("df -h")  # Example command to get disk usage info
        # disk_info = stdout.read().decode("utf-8")
        # stdin, stdout, stderr = ssh_client.exec_command("top -bn1")  # Example command to get CPU usage info
        # cpu_info = stdout.read().decode("utf-8")

        # # Close SSH connection
        # # ssh_client.close()
        # testin, testout, testerr = ssh_client.exec_command("ls")
        # print(testin.read().decode("utf-8") + testout.read().decode("utf-8") + testerr.read().decode("utf-8"))
        # # Return the collected information
        # return mem_info, disk_info, cpu_info

    except Exception as e:
        print("Error:", e)
        return None


def collect_system_info(hostname=DEFAULT_HOSTNAME):
    with app.app_context():
        hostname = shared.current_hostname
        username = shared.current_username
        password = shared.current_password
        if hostname == DEFAULT_HOSTNAME:
            virtual_memory = psutil.virtual_memory()
            disk_usage = psutil.disk_usage("/")
            cpu_times = psutil.cpu_times()

            memory_data = Memory(used=round(bytes_to_gb(virtual_memory.used),2), 
                                 active=round(bytes_to_gb(virtual_memory.active),2),
                                 inactive=round(bytes_to_gb(virtual_memory.inactive),2),
                                usage_percent=virtual_memory.percent,
                                host_ip=hostname)
            disk_data = Disk(used=round(bytes_to_gb(disk_usage.used),2),
                             free=round(bytes_to_gb(disk_usage.free ),2) ,
                            usage_percent=disk_usage.percent,
                            host_ip=hostname)
            cpu_data = Cpu(times_user=round(cpu_times.user,2),
                           times_system=round(cpu_times.system,2), 
                           times_idle=round(cpu_times.idle,2),
                            usage_percent=psutil.cpu_percent(interval=1),
                            host_ip=hostname)

            db.session.query(ActiveProcesses).delete()
            for proc in psutil.process_iter(attrs=['pid', 'name', 'status', 'create_time']):
                try:
                    pid = proc.info['pid']
                    name = proc.info['name']
                    status = proc.info['status']
                    start_date = datetime.fromtimestamp(proc.info['create_time']).strftime('%Y-%m-%d %H:%M:%S.%f')
                    process = ActiveProcesses(pid=pid, name=name, status=status, start_date=start_date, host_ip=hostname)
                    db.session.add(process)
                except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                    pass
            db.session.add(memory_data)
            db.session.add(disk_data)
            db.session.add(cpu_data)
            db.session.commit()

        else:
            ssh = paramiko.SSHClient()
            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            try:
                ssh.connect(hostname, username=username, password=password)
                collect_remote_system_info(ssh)
            
            except Exception as e:
                print("Error:", e)

            db.session.query(ActiveProcesses).delete()
            process = ActiveProcesses(id=0, measurement_time='s', pid=1234, name='process1', status='running', start_date='2024-02-13 12:00:00.000', host_ip=hostname)
            db.session.add(process)


def background_thread():
    while True:
        collect_system_info()
        time.sleep(10)


def start_background_thread():
    thread = threading.Thread(target=background_thread)
    thread.daemon = True
    thread.start()
start_background_thread()


@app.route('/change_hostname/<new_hostname>', methods=['POST'])
def change_hostname(new_hostname):
    shared.current_hostname = new_hostname
    hostname_changed_event.set()  
    return f"Hostname changed to {new_hostname}"


if __name__ == "__main__":
    app.run(debug=True, use_reloader=False)
