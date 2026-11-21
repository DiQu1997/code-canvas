# 自动售货机 —— 示例项目用的玩具代码，只为了让示例能从机器图一路钻到源码。
PRICES = {'cola': 3, 'water': 2}


class Machine:
    def __init__(self):
        self.balance = 0                        # 已经投进来、还没花掉的钱
        self.stock = {'cola': 5, 'water': 5}

    def insert(self, coin):
        # 只认 1 元和 5 元硬币。不找零，所以机器里不用备零钱。
        if coin not in (1, 5):
            return False
        self.balance += coin
        return True

    def buy(self, item):
        price = PRICES[item]
        if self.balance < price:                # 钱不够：什么都不出，余额不清零，等下一枚
            return None
        if self.stock[item] == 0:               # 没货：不转电机，也不扣钱
            return None
        self.dispense(item)                     # 先出货
        self.balance -= price                   # 后扣款：卡货时顾客不吃亏
        return item

    def dispense(self, item):
        self.stock[item] -= 1
        self.motor(item)

    def motor(self, item):
        pass                                    # 真机器在这里转电机
