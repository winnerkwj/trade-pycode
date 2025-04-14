import pyupbit
import websockets


# ------------------------------------------------------------
# 로그인
# ------------------------------------------------------------
key_file_path = r'/home/winnerkwj/trade/upbit_k.txt'

with open(key_file_path, 'r') as file:
    access = file.readline().strip()
    secret = file.readline().strip()

upbit = pyupbit.Upbit(access, secret)

