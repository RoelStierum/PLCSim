import socket
import subprocess
import time
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def is_port_in_use(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(('localhost', port)) == 0

def find_process_using_port(port):
    try:
        # Get all TCP connections
        result = subprocess.run(['netstat', '-ano'], capture_output=True, text=True)
        lines = result.stdout.split('\n')
        
        # Find the line with our port
        for line in lines:
            if f':{port}' in line and 'LISTENING' in line:
                # Extract the PID
                parts = line.strip().split()
                if len(parts) >= 5:
                    return parts[-1]
    except Exception as e:
        logger.error(f"Error finding process: {e}")
    return None

def kill_process(pid):
    try:
        subprocess.run(['taskkill', '/F', '/PID', pid], check=True)
        logger.info(f"Successfully killed process {pid}")
        return True
    except subprocess.CalledProcessError as e:
        logger.error(f"Failed to kill process {pid}: {e}")
        return False

def main():
    port = 4860
    logger.info(f"Checking port {port}...")
    
    if is_port_in_use(port):
        logger.info(f"Port {port} is in use")
        pid = find_process_using_port(port)
        if pid:
            logger.info(f"Found process {pid} using port {port}")
            if kill_process(pid):
                logger.info("Port should now be free")
                time.sleep(1)  # Wait a bit
                if not is_port_in_use(port):
                    logger.info("Port is now free")
                else:
                    logger.error("Port is still in use")
            else:
                logger.error("Failed to free up port")
        else:
            logger.error("Could not find process using port")
    else:
        logger.info(f"Port {port} is free")

if __name__ == "__main__":
    main() 