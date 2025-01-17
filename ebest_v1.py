import time
import win32com.client
import pythoncom
from config.errCode import *
from config.accountCalculator import *
from datetime import datetime
from datetime import timedelta
import collections
import math

###############################################################################
# 0) 보조 함수들: 에러코드 해석 등은 기존과 동일.
#    추가로 RSI 계산 함수를 포함.
###############################################################################

def calculate_rsi(price_list, period=14):
    """
    (간단 구현) RSI 계산 함수
    :param price_list: 종가(또는 틱 체결가) 리스트 (list or deque)
    :param period: RSI 기간 (기본 14)
    :return: RSI(float). 데이터가 부족하면 None
    """
    if len(price_list) < period:
        return None
    
    gains = []
    losses = []

    for i in range(1, len(price_list)):
        change = price_list[i] - price_list[i - 1]
        if change > 0:
            gains.append(change)
            losses.append(0)
        else:
            gains.append(0)
            losses.append(abs(change))

    # 최근 period개의 데이터만 사용
    gains = gains[-period:]
    losses = losses[-period:]

    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period

    if avg_loss == 0:
        return 100.0  # 손실이 전혀 없다면 RSI=100

    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    return rsi


###############################################################################
# 1) 사용자님이 제공하신 Object 클래스
###############################################################################
class Object:

    ##### 오브젝트 모음 #####
    # 우리가 요청하는 TR요청 설정 변수
    XAQuery_o3105 = None  # 종목정보 요청
    XAQuery_CIDBQ01500 = None # 해외선물 미결제 정보내역 요청
    XAQuery_CIDBQ03000 = None # 해외선물 예수금/잔고현황
    XAQuery_CIDBQ00100 = None # 해외선물 신규주문
    XAQuery_CIDBQ01000 = None # 해외선물 취소주문
    XAQuery_CIDBQ00900 = None # 해외선물 정정주문

    #실시간으로 수신되는 설정 변수
    XAReal_OVH = None # 종목 실시간 호가 정보 받기
    XAReal_OVC = None # 종목 실시간 틱봉 정보 받기
    XARealOrder_TC1 = None  # 주문 접수 데이터 받기
    XARealOrder_TC2 = None  # 주문 응답 데이터 받기
    XARealOrder_TC3 = None  # 주문 체결 데이터 받기
    ###########################

    ##### 함수를 할당한 변수 #####
    tr_signal_o3105 = None  # 종목정보tr요청 함수를 할당
    tr_signal_CIDBQ01500 = None # 미결제 함수를 할당
    tr_signal_CIDBQ03000 = None # 예수금/잔고현황 함수를 할당
    order_buy_CIDBT00100 = None # 해외선물 신규주문
    order_cancel_CIDBT01000 = None # 해외선물 취소주문
    ###########################

    ##### 기타 변수 모음 #####
    TR처리완료 = False
    로그인완료 = False
    해외선물_계좌번호 = None
    종목정보_딕셔너너리 = {}
    종목정보_딕셔너리 = {}  # (오탈자 수정)
    미결제_딕셔너리 = {}
    예수금_딕셔너리 = {}
    실시간호가_딕셔너리 = {}
    실시간체결_딕셔너리 = {}

    주문접수_딕셔너리 = {}
    주문응답_딕셔너리 = {}
    주문체결_딕셔너리 = {}
    #######################

    ##### 기타설정 #####
    매수 = False
    매도 = False
    취소 = False
    정정 = False
    ##################


###############################################################################
# 2) 로그인/연결 이벤트 클래스
###############################################################################
class XASessionEvent:

    def OnLogin(self, szCode, szMsg):
        print("★★★ 로그인 %s, %s" % (szCode, szMsg))

        if szCode == "0000":
            Object.로그인완료 = True
        else:
            Object.로그인완료 = False

    def OnDisconnect(self):
        print("★★★ 연결 끊김")


###############################################################################
# 3) 단일 TR 요청 이벤트 클래스 (XAQueryEvent)
###############################################################################
class XAQueryEvent:
    def OnReceiveData(self, szTrCode):

        if szTrCode == "o3105":
            print("★★★ 해외선물 종목정보 결과반환")

            종목코드 = self.GetFieldData("031050utBlock", "Symbol", 0)
            if 종목코드 != "":
                종목명 = self.GetFieldData("031050utBlock", "SymbolNm", 0)
                # 이하 쭉 종목 정보 로드
                # ...
                # 예시로 일부만 표시
                체결가격 = self.GetFieldData("031050utBlock", "TrdP", 0)
                체결수량 = self.GetFieldData("031050utBlock", "TrdQ", 0)

                if 종목코드 not in Object.종목정보_딕셔너리.keys():
                    Object.종목정보_딕셔너리.update({종목코드: {}})

                Object.종목정보_딕셔너리[종목코드].update({"종목코드": 종목코드})
                Object.종목정보_딕셔너리[종목코드].update({"종목명": 종목명})
                Object.종목정보_딕셔너리[종목코드].update({"체결가격": float(체결가격)})
                Object.종목정보_딕셔너리[종목코드].update({"체결수량": int(체결수량)})

                print("\n=====종목정보======== "
                      "\n%s"
                      "\n%s"
                      "\n====================="
                      % (종목코드, Object.종목정보_딕셔너리[종목코드]))

            Object.TR처리완료 = True

        elif szTrCode == "CIDBQ01500":
            print("★★★ 미결제잔고내역 조회")

            occurs_count = self.GetBlockCount("CIDBQ015000utBlock2")
            for i in range(occurs_count):
                종목코드값 = self.GetFieldData("CIDBQ015000utBlock2", "IsuCodeVal", i)
                잔고수량 = self.GetFieldData("CIDBQ015000utBlock2", "BalQty", i)
                매입가격 = self.GetFieldData("CIDBQ015000utBlock2", "PchsPrc", i)
                해외파생현재가 = self.GetFieldData("CIDBQ015000utBlock2", "OvrsDrvtNowPrc", i)

                if 종목코드값 not in Object.미결제_딕셔너리.keys():
                    Object.미결제_딕셔너리.update({종목코드값: {}})

                Object.미결제_딕셔너리[종목코드값].update({"잔고수량": int(잔고수량)})
                Object.미결제_딕셔너리[종목코드값].update({"매입가격": float(매입가격)})
                Object.미결제_딕셔너리[종목코드값].update({"해외파생현재가": float(해외파생현재가)})

                print("\n====== 미결제 ======="
                      "\n%s"
                      "\n%s"
                      "\n======================"
                      % (종목코드값, Object.미결제_딕셔너리[종목코드값]))

            if Object.XAQuery_CIDBQ01500.IsNext:
                Object.tr_signal_CIDBQ01500(IsNext=True)
            else:
                Object.TR처리완료 = True

        elif szTrCode == "CIDBQ03000":
            print("★★★ 해외선물 예수금/잔고현황")

            occurs_count = self.GetBlockCount("CIDBQ030000utBlock2")
            for i in range(occurs_count):
                통화대상코드 = self.GetFieldData("CIDBQ030000utBlock2", "CrcyObjCode", i)
                해외선물예수금 = self.GetFieldData("CIDBQ030000utBlock2", "OvrsFutsDps", i)

                if 통화대상코드 not in Object.예수금_딕셔너리.keys():
                    Object.예수금_딕셔너리.update({통화대상코드: {}})

                Object.예수금_딕셔너리[통화대상코드].update({"해외선물예수금": float(해외선물예수금)})

                print("\n===== 예수금 ========"
                      "\n%s"
                      "\n%s"
                      "\n===================="
                      % (통화대상코드, Object.예수금_딕셔너리[통화대상코드]))

            if Object.XAQuery_CIDBQ03000.IsNext:
                Object.tr_signal_CIDBQ03000(IsNext=True)
            else:
                Object.TR처리완료 = True

    def OnReceiveMessage(self, systemError, messageCode, message):
        if messageCode != "00000":
            if Object.취소 == True:
                Object.취소 = False
            if len(Object.주문접수_딕셔너리) == 0 and Object.매수 == True:
                Object.매수 = False
            if Object.정정 == True:
                Object.정정 = False
            elif Object.정정 == False:
                Object.매도 = False

        print("★★★ systemError: %s, messageCode: %s, message: %s" % (systemError, messageCode, message))


###############################################################################
# 4) 실시간 이벤트 클래스 (XARealEvent)
#    여기서 RSI 계산, 분봉 구성, 매매 로직 등을 추가
###############################################################################
class XARealEvent:
    # [추가] 분봉 관리용
    _tick_buffer = {}         # 분봉을 만들기 위해 틱 데이터를 임시 저장하는 버퍼
    _current_minute = {}      # 현재 분봉이 어느 minute인지 (HHMM 형식)
    
    # [추가] RSI 관련 파라미터 (사용자가 원하는 값으로 수정 가능)
    rsi_period = 14
    rsi_buy_threshold = 30.0
    rsi_sell_threshold = 70.0

    # [추가] 포지션/손익 관리
    position = 0         # 0: 무포지션, 1: 매수(롱), -1: 매도(숏)
    entry_price = None
    target_profit = 100.0     # 예시: +$100 달성 시 청산
    stop_loss = 50.0          # 예시: -$50 손실 시 청산
    target_profit_rate = 0.02 # 예시: +2% 수익이면 청산
    stop_loss_rate = -0.01    # 예시: -1% 손실이면 청산

    def OnReceiveRealData(self, trCode):

        # 실시간 호가
        if trCode == "OVH":
            종목코드 = self.GetFieldData("OutBlock", "symbol")
            호가시간 = self.GetFieldData("OutBlock", "hotime")
            매도호가1 = self.GetFieldData("OutBlock", "offerho1")
            매수호가1 = self.GetFieldData("OutBlock", "bidho1")
            # ... 기존 로직 그대로
            # 실시간호가_딕셔너리에 저장
            if 종목코드 not in Object.실시간호가_딕셔너리.keys():
                Object.실시간호가_딕셔너리.update({종목코드: {}})

            Object.실시간호가_딕셔너리[종목코드].update({"종목코드": 종목코드})
            Object.실시간호가_딕셔너리[종목코드].update({"호가시간": 호가시간})
            Object.실시간호가_딕셔너리[종목코드].update({"매도호가1": float(매도호가1)})
            Object.실시간호가_딕셔너리[종목코드].update({"매수호가1": float(매수호가1)})
            # ...

        # 실시간 체결
        elif trCode == "OVC":
            종목코드 = self.GetFieldData("OutBlock", "symbol")
            체결시간_한국 = self.GetFieldData("OutBlock", "kortm")  # HHMMSS
            체결가격 = self.GetFieldData("OutBlock", "curpr")      # 체결가격(스트링)

            if 종목코드 not in Object.실시간체결_딕셔너리.keys():
                Object.실시간체결_딕셔너리.update({종목코드: {}})

            Object.실시간체결_딕셔너리[종목코드].update({"종목코드": 종목코드})
            Object.실시간체결_딕셔너리[종목코드].update({"체결시간_한국": 체결시간_한국})
            Object.실시간체결_딕셔너리[종목코드].update({"체결가격": float(체결가격)})

            # [추가] 1분봉 처리
            price_float = float(체결가격)

            if 종목코드 not in self._tick_buffer:
                self._tick_buffer[종목코드] = []
                self._current_minute[종목코드] = None

            current_min_str = 체결시간_한국[:4]  # 예: HHMM
            if (self._current_minute[종목코드] is None) or (current_min_str != self._current_minute[종목코드]):
                # 이전 분봉이 끝났다면, 그 분봉 종가(또는 OHLC)로 RSI 계산
                if self._current_minute[종목코드] is not None:
                    self.on_minute_candle_close(종목코드)

                self._current_minute[종목코드] = current_min_str
                self._tick_buffer[종목코드] = []
            
            self._tick_buffer[종목코드].append(price_float)

    def on_minute_candle_close(self, 종목코드):
        """
        1분 완성 시점에 호출
        _tick_buffer[종목코드] -> 종가 계산 (OHLC 필요 시 각각 계산 가능)
        이후 RSI 계산 등 수행
        """
        tick_data = self._tick_buffer[종목코드]
        if len(tick_data) == 0:
            return
        
        _open = tick_data[0]
        _high = max(tick_data)
        _low = min(tick_data)
        _close = tick_data[-1]

        # 분봉 데이터 저장 (간단: 종가 위주)
        if 종목코드 not in Object.실시간체결_딕셔너리:
            Object.실시간체결_딕셔너리[종목코드] = {}
        if "candles" not in Object.실시간체결_딕셔너리[종목코드]:
            Object.실시간체결_딕셔너리[종목코드]["candles"] = collections.deque(maxlen=200)

        # 예시로 “종가”만 저장해서 RSI 계산
        Object.실시간체결_딕셔너리[종목코드]["candles"].append(_close)

        # RSI 계산
        close_list = list(Object.실시간체결_딕셔너리[종목코드]["candles"])
        rsi_val = calculate_rsi(close_list, period=self.rsi_period)

        if rsi_val is not None:
            print(f"[{종목코드}] 분봉 종가={_close:.2f}, RSI={rsi_val:.2f}")
            # RSI 매매 로직
            self.check_rsi_entry_exit(rsi_val, 종목코드, _close)

        # 손익 체크
        self.check_profit_stoploss(종목코드, _close)

    def check_rsi_entry_exit(self, rsi, 종목코드, current_price):
        """
        RSI 진입/청산 로직
        """
        if self.position == 0:
            # RSI 매수 시그널
            if rsi <= self.rsi_buy_threshold:
                print(f"[{종목코드}] RSI={rsi:.2f} -> 매수 진입!")
                self.position = 1
                self.entry_price = current_price
                # 실제 주문 Object.order_buy_CIDBT00100(...) 호출 가능

            # RSI 매도 시그널
            elif rsi >= self.rsi_sell_threshold:
                print(f"[{종목코드}] RSI={rsi:.2f} -> 매도 진입!")
                self.position = -1
                self.entry_price = current_price
                # 실제 주문 Object.order_buy_CIDBT00100(...) 호출 가능
        else:
            # 이미 포지션이 있다면, RSI 반전 시에 청산할 수도 있지만
            # 여기서는 단순 손익 체크(아래 check_profit_stoploss)로 처리
            pass

    def check_profit_stoploss(self, 종목코드, current_price):
        """
        목표 손익/손실 또는 수익률 도달 시 청산
        """
        if self.position == 0 or self.entry_price is None:
            return

        # 단순 금액 기준 손익
        pnl = (current_price - self.entry_price) * self.position
        # 해외선물 tick value 반영 필요 시, 추가 계산 로직이 필요할 수도 있음

        # 1) 금액 기준
        if pnl >= self.target_profit:
            print(f"[{종목코드}] +목표수익 {self.target_profit} 도달 -> 청산")
            self.close_position(종목코드)
        elif pnl <= -abs(self.stop_loss):
            print(f"[{종목코드}] -손실 {self.stop_loss} 초과 -> 청산")
            self.close_position(종목코드)

        # 2) 수익률 기준
        if self.entry_price != 0:
            profit_rate = pnl / self.entry_price
            if profit_rate >= self.target_profit_rate:
                print(f"[{종목코드}] +목표수익률 {self.target_profit_rate*100:.2f}% 도달 -> 청산")
                self.close_position(종목코드)
            elif profit_rate <= self.stop_loss_rate:
                print(f"[{종목코드}] -손실률 {self.stop_loss_rate*100:.2f}% 초과 -> 청산")
                self.close_position(종목코드)

    def close_position(self, 종목코드):
        """
        포지션 청산(매수 -> 매도 or 매도 -> 매수) 주문
        """
        if self.position != 0:
            if self.position > 0:
                print(f"[{종목코드}] 매수 포지션 청산 주문 (매도)")
                # 실제 청산: Object.order_buy_CIDBT00100(...)
            else:
                print(f"[{종목코드}] 매도 포지션 청산 주문 (매수)")
                # 실제 청산: Object.order_buy_CIDBT00100(...)

        # 포지션 정리
        self.position = 0
        self.entry_price = None


###############################################################################
# 5) 실시간으로 주문 정보 정의해주는 이벤트 클래스 (XARealOrderEvent)
###############################################################################
class XARealOrderEvent:
    def OnReceiveRealData(self, trCode):

        if trCode == "TC1":
            라인일련번호 = self.GetFieldData("OutBlock", "lineseq")
            주문번호 = self.GetFieldData("OutBlock", "ordr_no")
            종목코드 = self.GetFieldData("OutBlock", "is_cd")
            매도매수유형 = self.GetFieldData("OutBlock", "s_b_ccd")
            정정취소유형 = self.GetFieldData("OutBlock", "ordr_ccd")
            주문가격 = self.GetFieldData("OutBlock", "ordr_prc")
            주문수량 = self.GetFieldData("OutBlock", "ordr_q")

            if 주문번호 not in Object.주문접수_딕셔너리.keys():
                Object.주문접수_딕셔너리.update({주문번호: {}})

            Object.주문접수_딕셔너리[주문번호].update({"종목코드": 종목코드})
            Object.주문접수_딕셔너리[주문번호].update({"매도매수유형": 매도매수유형})
            Object.주문접수_딕셔너리[주문번호].update({"정정취소유형": 정정취소유형})
            Object.주문접수_딕셔너리[주문번호].update({"주문가격": float(주문가격)})
            Object.주문접수_딕셔너리[주문번호].update({"주문수량": int(주문수량)})

            print("\n===== 주문접수 ======="
                  f"\n주문번호: {주문번호}"
                  f"\n{Object.주문접수_딕셔너리[주문번호]}"
                  "\n===========================")

        elif trCode == "TC2":
            주문번호 = self.GetFieldData("OutBlock", "ordr_no")
            원주문번호 = self.GetFieldData("OutBlock", "orgn_ordr_no")
            정정취소유형 = self.GetFieldData("OutBlock", "ordr_ccd")

            if 주문번호 not in Object.주문응답_딕셔너리.keys():
                Object.주문응답_딕셔너리.update({주문번호: {}})

            Object.주문응답_딕셔너리[주문번호].update({"원주문번호": 원주문번호})
            Object.주문응답_딕셔너리[주문번호].update({"정정취소유형": 정정취소유형})

            print("\n=====주문응답======="
                  f"\n주문번호: {주문번호}"
                  f"\n{Object.주문응답_딕셔너리[주문번호]}"
                  "\n===================")

            if 원주문번호 in Object.주문접수_딕셔너리:
                del Object.주문접수_딕셔너너리[원주문번호]  # (오탈자 수정)
            if 원주문번호 in Object.주문접수_딕셔너리:
                del Object.주문접수_딕셔너리[원주문번호]  # 다시한번 체크

            del Object.주문응답_딕셔너리[주문번호]
            Object.매수 = False
            Object.취소 = False

        elif trCode == "TC3":
            주문번호 = self.GetFieldData("OutBlock", "ordr_no")
            원주문번호 = self.GetFieldData("OutBlock", "orgn_ordr_no")
            체결가격 = self.GetFieldData("OutBlock", "ccls_prc")
            체결수량 = self.GetFieldData("OutBlock", "ccls_q")
            매도매수유형 = self.GetFieldData("OutBlock", "s_b_ccd")

            if 주문번호 not in Object.주문체결_딕셔너리.keys():
                Object.주문체결_딕셔너리.update({주문번호: {}})

            Object.주문체결_딕셔너리[주문번호].update({"체결가격": float(체결가격)})
            Object.주문체결_딕셔너리[주문번호].update({"체결수량": int(체결수량)})
            Object.주문체결_딕셔너리[주문번호].update({"매도매수유형": 매도매수유형})

            print("\n===== 주문체결 ========"
                  f"\n주문번호: {주문번호}"
                  f"\n체결수량: {체결수량}"
                  f"\n{Object.주문체결_딕셔너리[주문번호]}"
                  "\n===================")

            if 주문번호 in Object.주문접수_딕셔너리:
                del Object.주문접수_딕셔너리[주문번호]

            Object.매수 = False

            # 체결 후 미결제 업데이트, 사용자님 코드에 있던 부분 생략 가능
            # ...


###############################################################################
# 6) 실제 XingApi_Class
###############################################################################
class XingApi_Class(Object):

    def __init__(self):

        ###### 함수모음 #####
        Object.tr_signal_o3105 = self.tr_signal_o3105
        Object.tr_signal_CIDBQ01500 = self.tr_signal_CIDBQ01500
        Object.tr_signal_CIDBQ03000 = self.tr_signal_CIDBQ03000
        Object.order_buy_CIDBT00100 = self.order_buy_CIDBT00100
        Object.order_cancel_CIDBT01000 = self.order_cancel_CIDBT01000
        Object.order_cancel_CIDBT00300 = self.order_cancel_CIDBT00900
        ####################

        ##### XASession COM 객체를 생성한다. ("API이벤트이름", 콜백클래스) #####
        self.XASession_object = win32com.client.DispatchWithEvents("XA_Session.XASession", XASessionEvent)
        ######################

        ##### Xing 모의서버에 연결 #####
        self.server_connect()
        ######################

        ##### 로그인 시도 #####
        self.login_connect_signal()
        ######################

        #### 계좌정보 #####
        self.get_account_info()
        ######################

        ##### 종목정보  #####
        Object.XAQuery_o3105 = win32com.client.DispatchWithEvents("XA_DataSet.XAQuery", XAQueryEvent)
        Object.XAQuery_o3105.ResFileName = "C:/LS_SEC/xingAPI/Res/o3105.res"
        ##################

        ##### 미결제  #####
        Object.XAQuery_CIDBQ01500 = win32com.client.DispatchWithEvents("XA_DataSet.XAQuery", XAQueryEvent)
        Object.XAQuery_CIDBQ01500.ResFileName = "C:/LS_SEC/xingAPI/Res/CIDBQ01500.res"
        ##################

        ##### 예수금/잔고현황 #####
        Object.XAQuery_CIDBQ03000 = win32com.client.DispatchWithEvents("XA_DataSet.XAQuery", XAQueryEvent)
        Object.XAQuery_CIDBQ03000.ResFileName = "C:/LS_SEC/xingAPI/Res/CIDBQ03000.res"
        ########################

        ##### TR 요청 (예시) #####
        self.tr_signal_CIDBQ01500()
        time.sleep(1.1)
        self.tr_signal_CIDBQ03000()
        time.sleep(1.1)
        self.tr_signal_o3105("HSIQ24")  # 예: 나스닥미니(가정)

        ##### XA_DataSet의 XAReal COM 객체 생성 #####
        Object.XAReal_OVH = win32com.client.DispatchWithEvents("XA_DataSet.XAReal", XARealEvent)
        Object.XAReal_OVH.ResFileName = "C:/LS_SEC/xingAPI/Res/OVH.res"
        Object.XAReal_OVC = win32com.client.DispatchWithEvents("XA_DataSet.XAReal", XARealEvent)
        Object.XAReal_OVC.ResFileName = "C:/LS_SEC/xingAPI/Res/OVC.res"

        time.sleep(1.1)
        self.set_real_signal(symbol="HSIQ24")

        ##해외선물 TR 신규주문 #####
        Object.XAQuery_CIDBT00100 = win32com.client.DispatchWithEvents("XA_DataSet.XAQuery", XAQueryEvent)
        Object.XAQuery_CIDBT00100.ResFileName = "C:/LS_SEC/xingAPI/Res/CIDBT00100.res"
        #####################

        ##### 해외선물 TR 취소주문 #####
        Object.XAQuery_CIDBT01000 = win32com.client.DispatchWithEvents("XA_DataSet.XAQuery", XAQueryEvent)
        Object.XAQuery_CIDBT01000.ResFileName = "C:/LS_SEC/xingAPI/Res/CIDBT01000.res"
        #####################

        ##### 해외선물 TR 정정주문 #####
        Object.XAQuery_CIDBT00900 = win32com.client.DispatchWithEvents("XA_DataSet.XAQuery", XAQueryEvent)
        Object.XAQuery_CIDBT00900.ResFileName = "C:/LS_SEC/xingAPI/Res/CIDBT00900.res"
        #####################

        ##### 주문접수 실시간 COM 객체 실행 #####
        Object.XARealOrder_TC1 = win32com.client.DispatchWithEvents("XA_DataSet.XAReal", XARealOrderEvent)
        Object.XARealOrder_TC1.ResFileName = "C:/LS_SEC/xingAPI/Res/TC1.res"
        Object.XARealOrder_TC1.AdviseRealData()
        #####################

        ##### 주문응답 실시간 COM 객체 실행 #####
        Object.XARealOrder_TC2 = win32com.client.DispatchWithEvents("XA_DataSet.XAReal", XARealOrderEvent)
        Object.XARealOrder_TC2.ResFileName = "C:/LS_SEC/xingAPI/Res/TC2.res"
        Object.XARealOrder_TC2.AdviseRealData()
        ######################

        ##### 주문체결 실시간 COM 객체 실행 #####
        Object.XARealOrder_TC3 = win32com.client.DispatchWithEvents("XA_DataSet.XAReal", XARealOrderEvent)
        Object.XARealOrder_TC3.ResFileName = "C:/LS_SEC/xingAPI/Res/TC3.res"
        Object.XARealOrder_TC3.AdviseRealData()
        ######################

        # 이벤트 루프
        while True:
            pythoncom.PumpWaitingMessages()

    # 서버접속 확인 함수
    def server_connect(self):
        print("★★★ 서버접속 확인 함수")
        if self.XASession_object.ConnectServer("demo.ebestsec.co.kr", 20001):
            print("★★★ 서버에 연결 됨")
        else:
            nErrCode = self.XASession_object.GetLastError()
            strErrMsg = self.XASession_object.GetErrorMessage(nErrCode)
            print(strErrMsg)

    # 로그인 시도 함수
    def login_connect_signal(self):
        print("★★★ 로그인 시도 함수")
        # (아이디, 비밀번호, 공인인증PW, 서버타입(사용안함), 에러표시여부)
        if self.XASession_object.Login("winnerkw", "4511kimL", "", 0, 0):
            print("★★★ 로그인 성공")

        while Object.로그인완료 == False:
            pythoncom.PumpWaitingMessages()

    # 계좌정보 가져오기 함수
    def get_account_info(self):
        print("★★★ 계좌정보 가져오기 함수")
        계좌수 = self.XASession_object.GetAccountListCount()

        for i in range(계좌수):
            계좌번호 = self.XASession_object.GetAccountList(i)
            if "5550" in 계좌번호:
                Object.해외선물_계좌번호 = 계좌번호

        print("★★ 해외선물 계좌번호 %s" % Object.해외선물_계좌번호)

    # 해외선물 종목정보 TR
    def tr_signal_o3105(self, symbol=None):
        print("★★★ tr_signal_o3105() 해외선물 종목정보 TR요청 %s" % symbol)
        Object.XAQuery_o3105.SetFieldData("o3105InBlock","symbol", 0, symbol)
        error = Object.XAQuery_o3105.Request(False)
        if error < 0:
            print("★★★ 에러코드 %s, 에러내용 %s" % (error, 에러코드(error)))
        Object.TR처리완료 = False
        while Object.TR처리완료 == False:
            pythoncom.PumpWaitingMessages()

    # 미결제 잔고내역
    def tr_signal_CIDBQ01500(self, IsNext=False):
        print("★★★ tr_signal_CIDBQ01500() 해외선물 미결제 잔고내역 TR요청")

        Object.XAQuery_CIDBQ01500.SetFieldData("CIDBQ01500InBlock1", "RecCnt", 0, 1)
        Object.XAQuery_CIDBQ01500.SetFieldData("CIDBQ01500InBlock1", "AcntTpCode", 0, "1")
        Object.XAQuery_CIDBQ01500.SetFieldData("CIDBQ01500InBlock1", "AcntNo", 0, Object.해외선물_계좌번호)
        Object.XAQuery_CIDBQ01500.SetFieldData("CIDBQ01500InBlock1", "FcmAcntNo", 0, "")
        Object.XAQuery_CIDBQ01500.SetFieldData("CIDBQ01500InBlock1", "Pwd", 0, "0000")
        Object.XAQuery_CIDBQ01500.SetFieldData("CIDBQ01500InBlock1", "QryDt", 0, "")
        Object.XAQuery_CIDBQ01500.SetFieldData("CIDBQ01500InBlock1", "BalTpCode", 0, "1")

        error = Object.XAQuery_CIDBQ01500.Request(IsNext)
        if error < 0:
            print("★★★ 에러코드 %s, 에러내용 %s" % (error, 에러코드(error)))

        Object.TR처리완료 = False
        while Object.TR처리완료 == False:
            pythoncom.PumpWaitingMessages()

    # 예수금/잔고현황
    def tr_signal_CIDBQ03000(self, IsNext=False):
        print("★★★ tr_signal_CIDBQ03000() 해외선물 예수금/잔고현황")

        now = datetime.now()
        date = now.strftime("%Y%m%d")

        Object.XAQuery_CIDBQ03000.SetFieldData("CIDBQ03000InBlock1", "RecCnt", 0, 1)
        Object.XAQuery_CIDBQ03000.SetFieldData("CIDBQ03000InBlock1", "AcntTpCode", 0, "1")
        Object.XAQuery_CIDBQ03000.SetFieldData("CIDBQ03000InBlock1", "AcntNo", 0, Object.해외선물_계좌번호)
        Object.XAQuery_CIDBQ03000.SetFieldData("CIDBQ03000InBlock1", "AcntPwd", 0, "0000")
        Object.XAQuery_CIDBQ03000.SetFieldData("CIDBQ03000InBlock1", "TrdDt", 0, date)

        error = Object.XAQuery_CIDBQ03000.Request(IsNext)
        if error < 0:
            print("★★★ 에러코드 %s, 에러내용 %s" % (error, 에러코드(error)))

        Object.TR처리완료 = False
        while Object.TR처리완료 == False:
            pythoncom.PumpWaitingMessages()

    # 실시간 호가/체결
    def set_real_signal(self, symbol=None):
        print("★★★ set_real_signal() 해외선물 호가/체결정보 실시간요청 %s" % symbol)

        Object.XAReal_OVH.SetFieldData("InBlock", "symbol", symbol)
        Object.XAReal_OVH.AdviseRealData()

        Object.XAReal_OVC.SetFieldData("InBlock", "symbol", symbol)
        Object.XAReal_OVC.AdviseRealData()

    # 해외선물 신규주문
    def order_buy_CIDBT00100(self, 레코드갯수=1, 주문일자=None, 지점코드=None, 계좌번호=None,
                             비밀번호=None, 종목코드값=None, 선물주문구분코드=None,
                             매매구분코드=None, 해외선물주문유형코드=None, 통화코드=None,
                             해외파생주문가격=0, 조건주문가격=0, 주문수량=0, 상품코드=None, 만기년월=None, 거래소코드=None):

        print("★★★ order_buy_CIDBT00100() 해외선물 신규주문")

        Object.XAQuery_CIDBT00100.SetFieldData("CIDBT00100InBlock1", "RecCnt", 0, 레코드갯수)
        Object.XAQuery_CIDBT00100.SetFieldData("CIDBT00100InBlock1", "OrdDt", 0, 주문일자)
        Object.XAQuery_CIDBT00100.SetFieldData("CIDBT00100InBlock1", "BrnCode", 0, 지점코드)
        Object.XAQuery_CIDBT00100.SetFieldData("CIDBT00100InBlock1", "AcntNo", 0, 계좌번호)
        Object.XAQuery_CIDBT00100.SetFieldData("CIDBT00100InBlock1", "Pwd", 0, 비밀번호)
        Object.XAQuery_CIDBT00100.SetFieldData("CIDBT00100InBlock1", "IsuCodeVal", 0, 종목코드값)
        Object.XAQuery_CIDBT00100.SetFieldData("CIDBT00100InBlock1", "FutsordTpCode", 0, 선물주문구분코드)
        Object.XAQuery_CIDBT00100.SetFieldData("CIDBT00100InBlock1", "BnsTpCode", 0, 매매구분코드)
        Object.XAQuery_CIDBT00100.SetFieldData("CIDBT00100InBlock1", "AbrdFutsOrdPtnCode", 0, 해외선물주문유형코드)
        Object.XAQuery_CIDBT00100.SetFieldData("CIDBT00100InBlock1", "CrcyCode", 0, 통화코드)
        Object.XAQuery_CIDBT00100.SetFieldData("CIDBT00100InBlock1", "OvrsDrvtordPrc", 0, 해외파생주문가격)
        Object.XAQuery_CIDBT00100.SetFieldData("CIDBT00100InBlock1", "CndiordPrc", 0, 조건주문가격)
        Object.XAQuery_CIDBT00100.SetFieldData("CIDBT00100InBlock1", "OrdQty", 0, 주문수량)
        Object.XAQuery_CIDBT00100.SetFieldData("CIDBT00100InBlock1", "PrdtCode",0, 상품코드)
        Object.XAQuery_CIDBT00100.SetFieldData("CIDBT00100InBlock1", "DueYymm", 0, 만기년월)
        Object.XAQuery_CIDBT00100.SetFieldData("CIDBT00100InBlock1", "ExchCode", 0, 거래소코드)

        error = Object.XAQuery_CIDBT00100.Request(False)
        if error < 0:
            print("order_buy_CIDBT00100 에러코드 %s, 에러내용 %s" % (error, 에러코드(error)))
            Object.매수 = False

    # 해외선물 취소주문
    def order_cancel_CIDBT01000(self, 레코드갯수=1, 주문일자=None, 지점번호=None,
                                계좌번호=None, 비밀번호=None, 종목코드값=None, 해외선물원주문번호=None,
                                선물주문구분코드=None, 상품구분코드=None, 거래소코드=None):
        print("★★★ order_buy_CIDBT01000() 해외선물 취소주문")

        Object.XAQuery_CIDBT01000.SetFieldData("CIDBT01000InBlock1", "RecCnt", 0, 레코드갯수)
        Object.XAQuery_CIDBT01000.SetFieldData("CIDBT01000InBlock1", "OrdDt", 0, 주문일자)
        Object.XAQuery_CIDBT01000.SetFieldData("CIDBT01000InBlock1", "BrnNo", 0, 지점번호)
        Object.XAQuery_CIDBT01000.SetFieldData("CIDBT01000InBlock1", "AcntNo", 0, 계좌번호)
        Object.XAQuery_CIDBT01000.SetFieldData("CIDBT01000InBlock1", "Pwd", 0, 비밀번호)
        Object.XAQuery_CIDBT01000.SetFieldData("CIDBT01000InBlock1", "IsuCodeVal", 0, 종목코드값)
        Object.XAQuery_CIDBT01000.SetFieldData("CIDBT01000InBlock1", "OvrsFutsorg0rdNo", 0, 해외선물원주문번호)
        Object.XAQuery_CIDBT01000.SetFieldData("CIDBT01000InBlock1", "FutsOrdTpCode", 0, 선물주문구분코드)
        Object.XAQuery_CIDBT01000.SetFieldData("CIDBT01000InBlock1", "PrdtTpCode", 0, 상품구분코드)
        Object.XAQuery_CIDBT01000.SetFieldData("CIDBT01000InBlock1", "ExchCode", 0, 거래소코드)

        error = Object.XAQuery_CIDBT01000.Request(False)
        if error < 0:
            print("order_buy_CIDBT01000 에러코드 %s, 에러내용 %s" % (error, 에러코드(error)))
            Object.취소 = False

    # 해외선물 정정주문
    def order_cancel_CIDBT00900(self, 레코드갯수=1, 주문일자=None, 등록지점번호=None,
                                계좌번호=None, 비밀번호=None, 해외선물원주문번호=None, 종목코드값=None,
                                선물주문구분코드=None, 매매구분코드=None, 선물주문유형코드=None, 통화코드값=None,
                                해외파생주문가격=None, 조건주문가격=None, 주문수량=None,
                                해외파생상품코드=None, 만기년월=None, 거래소코드=None):
        print("★★★ order_buy_CIDBT00900() 해외선물 정정주문")

        Object.XAQuery_CIDBT00900.SetFieldData("CIDBT00900InBlock1", "RecCnt", 0, 레코드갯수)
        Object.XAQuery_CIDBT00900.SetFieldData("CIDBT00900InBlock1", "OrdDt", 0, 주문일자)
        Object.XAQuery_CIDBT00900.SetFieldData("CIDBT00900InBlock1", "RegBrnNo", 0, 등록지점번호)
        Object.XAQuery_CIDBT00900.SetFieldData("CIDBT00900InBlock1", "AcntNo", 0, 계좌번호)
        Object.XAQuery_CIDBT00900.SetFieldData("CIDBT00900InBlock1", "Pwd", 0, 비밀번호)
        Object.XAQuery_CIDBT00900.SetFieldData("CIDBT00900InBlock1", "OvrsFutsOrgOrdNo", 0, 해외선물원주문번호)
        Object.XAQuery_CIDBT00900.SetFieldData("CIDBT00900InBlock1", "IsuCodeVal", 0, 종목코드값)
        Object.XAQuery_CIDBT00900.SetFieldData("CIDBT00900InBlock1", "FutsOrdTpCode", 0, 선물주문구분코드)
        Object.XAQuery_CIDBT00900.SetFieldData("CIDBT00900InBlock1", "BnsTpCode", 0, 매매구분코드)
        Object.XAQuery_CIDBT00900.SetFieldData("CIDBT00900InBlock1", "Futs0rdPtnCode", 0, 선물주문유형코드)
        Object.XAQuery_CIDBT00900.SetFieldData("CIDBT00900InBlock1", "CrcyCodeVal", 0, 통화코드값)
        Object.XAQuery_CIDBT00900.SetFieldData("CIDBT00900InBlock1", "OvrsDrvtOrdPrc", 0, 해외파생주문가격)
        Object.XAQuery_CIDBT00900.SetFieldData("CIDBT00900InBlock1", "CndiordPrc", 0, 조건주문가격)
        Object.XAQuery_CIDBT00900.SetFieldData("CIDBT00900InBlock1", "OrdQty", 0, 주문수량)
        Object.XAQuery_CIDBT00900.SetFieldData("CIDBT00900InBlock1", "OvrsDrvtPrdtCode", 0, 해외파생상품코드)
        Object.XAQuery_CIDBT00900.SetFieldData("CIDBT00900InBlock1", "DueYymm", 0, 만기년월)
        Object.XAQuery_CIDBT00900.SetFieldData("CIDBT00900InBlock1", "ExchCode", 0, 거래소코드)

        error = Object.XAQuery_CIDBT00900.Request(False)
        if error < 0:
            print("order_buy_CIDBT00900 에러코드 %s, 에러내용 %s" % (error, 에러코드(error)))
            Object.정정 = False


if __name__ == "__main__":
    XingApi_Class()
