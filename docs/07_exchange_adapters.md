# 07 · 交易所适配层 · 完整设计

> **范围**:9 家交易所统一接入(5 CEX + 2 DEX 永续 + 2 DEX 现货)
> **目标**:对所有策略提供**统一的接口**,屏蔽各交易所差异

---

## 一、为什么需要适配层

### 1.1 问题:每家交易所 API 都不一样

如果策略代码直接调用各交易所 API,会出现:

```python
# 反例:策略代码绑死特定交易所
class FundingStrategy:
    async def open_position(self):
        # Binance 风格
        await binance_client.futures_create_order(
            symbol="BTCUSDT", side="SELL", type="LIMIT", ...
        )
        # Bybit 风格(不一样!)
        await bybit_client.place_active_order(
            symbol="BTCUSDT", side="Sell", order_type="Limit", ...
        )
        # OKX 风格(又不一样!)
        await okx_client.trade_order(
            instId="BTC-USDT-SWAP", side="sell", ordType="limit", ...
        )
```

**问题**:
- 加新交易所要改所有策略代码
- 测试时 mock 一堆不同的 API
- 任何一家交易所 API 变更,所有策略都受影响

### 1.2 解决:统一 ExchangeAdapter 接口

```python
# 正例:策略只用统一接口
class FundingStrategy:
    async def open_position(self, exchange: str):
        adapter = self.adapters[exchange]   # binance / bybit / okx 都一样
        await adapter.place_order(
            symbol=Symbol("BTC", "USDT"),
            side=Side.SELL,
            order_type=OrderType.LIMIT,
            ...
        )
```

底层适配器把统一接口翻译成各家 API 的具体调用,**策略完全不知道下面是哪家交易所**。

---

## 二、统一接口规范

### 2.1 ExchangeAdapter 抽象基类

每个交易所的适配器必须实现这些方法:

```python
class ExchangeAdapter(ABC):

    # ========== 元数据 ==========
    @property
    @abstractmethod
    def exchange_name(self) -> str: ...

    @property
    @abstractmethod
    def supported_instruments(self) -> List[InstrumentType]: ...
    # 例如 [SPOT, PERPETUAL, QUARTERLY, OPTION]

    # ========== 行情数据(只读) ==========
    @abstractmethod
    async def fetch_ticker(self, symbol: Symbol) -> Ticker: ...

    @abstractmethod
    async def fetch_orderbook(
        self, symbol: Symbol, depth: int = 10
    ) -> OrderBook: ...

    @abstractmethod
    async def fetch_funding_rate(self, symbol: Symbol) -> FundingRate: ...

    @abstractmethod
    async def fetch_klines(
        self, symbol: Symbol, interval: str, limit: int
    ) -> List[Kline]: ...

    # ========== 账户数据(需 API key) ==========
    @abstractmethod
    async def fetch_balance(self) -> Balance: ...

    @abstractmethod
    async def fetch_positions(self) -> List[Position]: ...

    @abstractmethod
    async def fetch_open_orders(
        self, symbol: Optional[Symbol] = None
    ) -> List[Order]: ...

    # ========== 交易操作 ==========
    @abstractmethod
    async def place_order(
        self,
        symbol: Symbol,
        side: Side,
        order_type: OrderType,
        size: Decimal,
        price: Optional[Decimal] = None,
        time_in_force: TimeInForce = TimeInForce.GTC,
        reduce_only: bool = False,
        post_only: bool = False,
    ) -> Order: ...

    @abstractmethod
    async def cancel_order(self, order_id: str, symbol: Symbol) -> bool: ...

    @abstractmethod
    async def cancel_all_orders(self, symbol: Optional[Symbol] = None) -> int: ...

    # ========== WebSocket 订阅 ==========
    @abstractmethod
    async def subscribe_orderbook(
        self, symbol: Symbol, callback: Callable
    ) -> Subscription: ...

    @abstractmethod
    async def subscribe_trades(
        self, symbol: Symbol, callback: Callable
    ) -> Subscription: ...

    @abstractmethod
    async def subscribe_user_data(self, callback: Callable) -> Subscription: ...

    # ========== 健康检查 ==========
    @abstractmethod
    async def ping(self) -> int: ...   # 返回延迟 ms

    @abstractmethod
    async def get_server_time(self) -> int: ...
```

### 2.2 统一数据模型

```python
@dataclass
class Symbol:
    base: str       # "BTC"
    quote: str      # "USDT"

    @property
    def native_format(self, exchange: str) -> str:
        # Binance: "BTCUSDT"
        # OKX: "BTC-USDT"
        # Bybit: "BTCUSDT"
        ...

@dataclass
class Ticker:
    symbol: Symbol
    bid: Decimal
    ask: Decimal
    last: Decimal
    volume_24h: Decimal
    timestamp: int

@dataclass
class OrderBook:
    symbol: Symbol
    bids: List[Tuple[Decimal, Decimal]]    # [(price, size), ...]
    asks: List[Tuple[Decimal, Decimal]]
    timestamp: int

@dataclass
class FundingRate:
    symbol: Symbol
    rate: Decimal
    next_funding_time: int
    funding_interval_hours: int       # 8 或 1

@dataclass
class Order:
    order_id: str
    symbol: Symbol
    side: Side
    order_type: OrderType
    size: Decimal
    price: Decimal
    filled: Decimal
    status: OrderStatus           # PENDING / FILLED / PARTIAL / CANCELED
    timestamp: int

@dataclass
class Position:
    symbol: Symbol
    side: Side
    size: Decimal
    entry_price: Decimal
    margin: Decimal
    unrealized_pnl: Decimal
    leverage: Decimal

class Side(Enum):
    BUY = "buy"
    SELL = "sell"

class OrderType(Enum):
    MARKET = "market"
    LIMIT = "limit"
    STOP_MARKET = "stop_market"
    STOP_LIMIT = "stop_limit"
```

---

## 三、CEX 适配(基于 CCXT)

### 3.1 为什么用 CCXT

[CCXT](https://github.com/ccxt/ccxt) 是 Python/JS/PHP 库,封装了 100+ 家交易所的 API。优势:

- ✅ 已经处理了大部分 API 差异
- ✅ 维护活跃,支持几乎所有主流交易所
- ✅ 异步版本(`ccxt.async_support`)对接 asyncio 完美
- ✅ 大量样板代码已写好(签名、限频、重试)

**但 CCXT 不是万能的**:
- ❌ WebSocket 部分**不完善**(尤其 ccxt.pro 是收费版)
- ❌ 一些交易所的特殊功能 CCXT 没暴露
- ❌ 某些情况下需要降级到原生 SDK

**我们的做法**:
- **REST API 用 CCXT**(节省 80% 工作量)
- **WebSocket 用各交易所原生 SDK**(性能、可靠性更好)
- **特殊功能(如 Binance 子账户)用原生 API**

### 3.2 CCXT 包装层

```python
class CCXTAdapter(ExchangeAdapter):
    """所有 CEX 共享的 CCXT 包装"""

    def __init__(self, exchange_id: str, api_key: str, api_secret: str, **kwargs):
        # 动态创建 CCXT 实例
        exchange_class = getattr(ccxt.async_support, exchange_id)
        self.client = exchange_class({
            'apiKey': api_key,
            'secret': api_secret,
            'enableRateLimit': True,
            **kwargs
        })
        self.exchange_id = exchange_id

    async def fetch_ticker(self, symbol: Symbol) -> Ticker:
        ccxt_symbol = self._to_ccxt_symbol(symbol)
        ticker = await self.client.fetch_ticker(ccxt_symbol)
        return Ticker(
            symbol=symbol,
            bid=Decimal(str(ticker['bid'])),
            ask=Decimal(str(ticker['ask'])),
            last=Decimal(str(ticker['last'])),
            volume_24h=Decimal(str(ticker['quoteVolume'])),
            timestamp=ticker['timestamp']
        )

    async def place_order(self, symbol: Symbol, side: Side, ...) -> Order:
        ccxt_params = {
            'symbol': self._to_ccxt_symbol(symbol),
            'type': order_type.value,
            'side': side.value,
            'amount': float(size),
            'price': float(price) if price else None,
            'params': {
                'postOnly': post_only,
                'reduceOnly': reduce_only,
            }
        }
        result = await self.client.create_order(**ccxt_params)
        return self._to_unified_order(result)

    # ... 其他方法

    def _to_ccxt_symbol(self, symbol: Symbol) -> str:
        # CCXT 统一格式:"BTC/USDT"
        return f"{symbol.base}/{symbol.quote}"
```

### 3.3 各 CEX 的特殊处理

#### Binance

```python
class BinanceAdapter(CCXTAdapter):
    def __init__(self, ...):
        super().__init__('binance', ...)
        # Binance 现货和合约是不同的"client"
        self.spot_client = ccxt.async_support.binance(...)
        self.usdm_client = ccxt.async_support.binanceusdm(...)
        self.coinm_client = ccxt.async_support.binancecoinm(...)

    async def fetch_ticker(self, symbol: Symbol, instrument: InstrumentType):
        if instrument == InstrumentType.SPOT:
            client = self.spot_client
        elif instrument == InstrumentType.PERPETUAL:
            client = self.usdm_client
        # ... 路由到正确的 client
```

#### Bybit

```python
class BybitAdapter(CCXTAdapter):
    def __init__(self, ...):
        super().__init__('bybit', ...)

    # Bybit 的特殊:统一账户(Unified Trading Account)
    # 现货和合约共享保证金,需要不同的处理
```

#### OKX

```python
class OKXAdapter(CCXTAdapter):
    def __init__(self, ...):
        # OKX 需要 passphrase(除了 key 和 secret)
        super().__init__('okx', ..., password=passphrase)

    # OKX symbol 格式:"BTC-USDT-SWAP"(永续) / "BTC-USDT"(现货)
```

#### HTX(原 Huobi)

```python
class HTXAdapter(CCXTAdapter):
    def __init__(self, ...):
        super().__init__('htx', ...)
        # ⚠️ HTX 在 2023 年改名,CCXT 中可能仍叫 'huobi'
        # 需要检查当前 CCXT 版本支持的 ID
```

#### Bitget

```python
class BitgetAdapter(CCXTAdapter):
    def __init__(self, ...):
        # Bitget 也需要 passphrase
        super().__init__('bitget', ..., password=passphrase)
```

### 3.4 WebSocket 实现

CCXT.pro(收费版)有 WS,但我们用免费方案——**直接用 websockets 库 + 各交易所文档**:

```python
class BinanceWebSocketClient:
    BASE_URL_SPOT = "wss://stream.binance.com:9443"
    BASE_URL_USDM = "wss://fstream.binance.com"

    async def subscribe_orderbook(self, symbol: Symbol, callback):
        ws_url = f"{self.BASE_URL_USDM}/ws/{symbol.lower()}@depth10@100ms"
        async with websockets.connect(ws_url) as ws:
            async for msg in ws:
                data = json.loads(msg)
                ob = OrderBook(
                    symbol=symbol,
                    bids=[(Decimal(p), Decimal(q)) for p, q in data['bids']],
                    asks=[(Decimal(p), Decimal(q)) for p, q in data['asks']],
                    timestamp=data['E']
                )
                await callback(ob)

    async def subscribe_user_data(self, callback):
        # 1. 调 REST API 获取 listenKey
        listen_key = await self._get_listen_key()
        # 2. 用 listenKey 连接私有 WS
        ws_url = f"{self.BASE_URL_USDM}/ws/{listen_key}"
        # 3. 每 30 分钟续期 listenKey
        # ...
```

### 3.5 限频管理

每家交易所都有 API 限频规则。**CCXT 自带 `enableRateLimit: True`**,会自动延迟避免被封。

但**还需要额外的应用层限频**:

```python
class RateLimiter:
    """应用层限频,比 CCXT 更激进的保护"""

    def __init__(self, max_requests_per_minute: int):
        self.max_rpm = max_requests_per_minute
        self.bucket = TokenBucket(max_rpm, max_rpm / 60)

    async def acquire(self):
        await self.bucket.consume(1)

# 使用
binance_rate_limiter = RateLimiter(1200)   # Binance 1200 req/min
async def place_order_safe(...):
    await binance_rate_limiter.acquire()
    return await binance_adapter.place_order(...)
```

---

## 四、DEX 永续适配

### 4.1 Hyperliquid

**特点**:
- 链上永续 DEX,但提供**完整的 REST + WebSocket API**
- 不需要直接跟智能合约交互(简化大量工作)
- API 风格类似 CEX

**Python SDK**:[hyperliquid-python-sdk](https://github.com/hyperliquid-dex/hyperliquid-python-sdk)

```python
from hyperliquid.exchange import Exchange
from hyperliquid.info import Info

class HyperliquidAdapter(ExchangeAdapter):
    def __init__(self, wallet_address: str, private_key: str):
        self.info = Info()                          # 公开数据,无需认证
        self.exchange = Exchange(private_key, ...)  # 交易,需要私钥

    async def fetch_funding_rate(self, symbol: Symbol):
        meta = self.info.meta()
        # Hyperliquid 资金费率每小时结算,需要从 meta 提取
        ...

    async def place_order(self, symbol: Symbol, side: Side, ...):
        # Hyperliquid 用 EIP-712 签名,SDK 已封装
        order = self.exchange.order(
            coin=symbol.base,
            is_buy=(side == Side.BUY),
            sz=float(size),
            limit_px=float(price),
            order_type={"limit": {"tif": "Gtc"}}
        )
```

**特殊处理**:
- 资金费率每小时一次(不是 8 小时)
- 有自己的"vault"概念(可以委托资金)

### 4.2 dYdX v4

**特点**:
- 已迁移到自己的 Cosmos 链(dYdX Chain)
- API 完整,但与 v3 完全不同
- 需要 Cosmos 钱包(不是 EVM)

**Python SDK**:[v4-client-py](https://github.com/dydxprotocol/v4-clients)

```python
from dydx_v4_client import NodeClient
from dydx_v4_client.network import TESTNET, MAINNET

class DydxAdapter(ExchangeAdapter):
    def __init__(self, mnemonic: str):
        # dYdX 用 mnemonic(助记词)
        self.node = NodeClient.connect(MAINNET)
        self.wallet = Wallet.from_mnemonic(self.node, mnemonic)

    async def place_order(self, symbol: Symbol, side: Side, ...):
        # dYdX v4 是基于 Cosmos 的,签名机制不同
        ...
```

**特殊处理**:
- 用 mnemonic 而不是 private key
- 资金费率每小时一次
- 流动性比 Hyperliquid 差,但更去中心化

---

## 五、DEX 现货适配(只读监控)

⚠️ **重要**:Uniswap 和 PancakeSwap 在我们系统中**只用于监控价差**,不主动下单。所以适配器只需要实现**读取价格**的方法。

### 5.1 Uniswap V3

**Python 库**:`web3.py`

```python
from web3 import Web3

class UniswapV3Adapter(ExchangeAdapter):
    QUOTER_ADDRESS = "0x61fFE014bA17989E743c5F6cB21bF9697530B21e"

    def __init__(self, rpc_url: str, chain: str):
        self.w3 = Web3(Web3.HTTPProvider(rpc_url))
        self.chain = chain   # ethereum / arbitrum / base
        self.quoter = self.w3.eth.contract(
            address=self.QUOTER_ADDRESS,
            abi=QUOTER_V2_ABI
        )

    async def fetch_swap_price(
        self, token_in: str, token_out: str, amount_in: Decimal, fee_tier: int = 3000
    ) -> Decimal:
        """模拟 swap,获取真实可成交价(包含滑点)"""
        amount_out = self.quoter.functions.quoteExactInputSingle(
            token_in, token_out, fee_tier, int(amount_in), 0
        ).call()
        return Decimal(amount_out) / Decimal(amount_in)
```

**关键**:用 Quoter 合约**模拟** swap,得到真实可成交价。**不要**直接读取池子的 sqrtPriceX96,那是无视滑点的中间价。

### 5.2 PancakeSwap V3

```python
class PancakeV3Adapter(UniswapV3Adapter):
    """PancakeSwap V3 复制了 Uniswap V3 的代码,几乎一样"""
    QUOTER_ADDRESS = "0xB048Bbc1Ee6b733FFfCFb9e9CeF7375518e25997"  # BSC

    def __init__(self, rpc_url: str):
        super().__init__(rpc_url, chain="bsc")
```

---

## 六、连接池与故障转移

### 6.1 多 RPC 节点

```python
class RPCPool:
    """多个 RPC 节点轮询,故障自动切换"""

    def __init__(self, urls: List[str]):
        self.urls = urls
        self.current = 0
        self.failed = set()

    async def call(self, method: str, *args):
        for attempt in range(len(self.urls)):
            url = self.urls[self.current]
            try:
                return await self._call_via_url(url, method, *args)
            except Exception as e:
                self.failed.add(self.current)
                self.current = (self.current + 1) % len(self.urls)
                if len(self.failed) == len(self.urls):
                    raise RuntimeError("All RPCs failed")
```

**配置**:

```yaml
exchanges:
  ethereum_rpcs:
    - https://eth-mainnet.alchemyapi.io/v2/${ALCHEMY_KEY}
    - https://mainnet.infura.io/v3/${INFURA_KEY}
    - https://eth.llamarpc.com           # 备用,免费
```

### 6.2 CEX API 故障处理

```python
class ResilientExchangeAdapter(ExchangeAdapter):
    """带重试和熔断的包装"""

    async def place_order(self, ...):
        for attempt in range(3):
            try:
                return await self.inner.place_order(...)
            except ccxt.NetworkError:
                await asyncio.sleep(0.5 * 2 ** attempt)
            except ccxt.ExchangeError as e:
                # 业务错误,不重试
                raise
            except ccxt.RateLimitExceeded:
                await asyncio.sleep(5)
        raise RuntimeError("Order failed after 3 attempts")
```

---

## 七、配置文件

```yaml
# config/exchanges.yaml

exchanges:
  binance:
    enabled: true
    type: cex
    rate_limit_per_minute: 1200
    api_key: ${BINANCE_API_KEY}        # 从 .env 读取
    api_secret: ${BINANCE_API_SECRET}
    instruments: [spot, perpetual]
    websocket:
      reconnect_interval_seconds: 5
      heartbeat_interval_seconds: 30

  bybit:
    enabled: true
    type: cex
    rate_limit_per_minute: 600
    api_key: ${BYBIT_API_KEY}
    api_secret: ${BYBIT_API_SECRET}
    instruments: [spot, perpetual]

  okx:
    enabled: true
    type: cex
    rate_limit_per_minute: 600
    api_key: ${OKX_API_KEY}
    api_secret: ${OKX_API_SECRET}
    api_passphrase: ${OKX_API_PASSPHRASE}
    instruments: [spot, perpetual, quarterly, option]

  htx:
    enabled: true
    type: cex
    rate_limit_per_minute: 800
    api_key: ${HTX_API_KEY}
    api_secret: ${HTX_API_SECRET}

  bitget:
    enabled: true
    type: cex
    rate_limit_per_minute: 600
    api_key: ${BITGET_API_KEY}
    api_secret: ${BITGET_API_SECRET}
    api_passphrase: ${BITGET_API_PASSPHRASE}

  hyperliquid:
    enabled: true
    type: dex_perp
    chain: hyperliquid
    wallet_address: ${TRADING_WALLET_ADDRESS}
    private_key: ${TRADING_WALLET_PRIVATE_KEY}

  dydx:
    enabled: true
    type: dex_perp
    chain: dydx
    mnemonic: ${DYDX_MNEMONIC}

  uniswap_v3_eth:
    enabled: true
    type: dex_spot
    chain: ethereum
    rpcs:
      - ${ETH_RPC_URL}
      - https://eth.llamarpc.com
    monitor_only: true                  # 只监控,不交易

  uniswap_v3_arb:
    enabled: true
    type: dex_spot
    chain: arbitrum
    rpcs:
      - ${ARBITRUM_RPC_URL}
    monitor_only: true

  pancake_v3:
    enabled: true
    type: dex_spot
    chain: bsc
    rpcs:
      - ${BSC_RPC_URL}
    monitor_only: true
```

---

## 八、错误处理标准

### 8.1 错误类型

```python
class ExchangeError(Exception):
    """所有适配器错误的基类"""

class NetworkError(ExchangeError):
    """网络问题,可重试"""

class RateLimitError(ExchangeError):
    """限频,等待后重试"""

class AuthError(ExchangeError):
    """认证失败,严重,通知用户"""

class InsufficientBalanceError(ExchangeError):
    """余额不足"""

class OrderRejectedError(ExchangeError):
    """订单被拒(参数错误、最小单量、价格偏离过大等)"""

class ExchangeMaintenanceError(ExchangeError):
    """交易所维护中"""
```

### 8.2 错误传播规则

| 错误类型 | 重试次数 | 是否触发风控 |
|---|---|---|
| NetworkError | 3 | 累计 3 次/5分钟触发 Tier 3 熔断 |
| RateLimitError | 等待重试 | 否 |
| AuthError | 不重试 | **立即停止该交易所** |
| InsufficientBalanceError | 不重试 | 通知策略调整仓位 |
| OrderRejectedError | 不重试 | 记录日志,可能修正后重试 |
| ExchangeMaintenanceError | 5 分钟后重试 | 记录,通知 |

---

## 九、测试要求

### 9.1 单元测试

每个适配器必须有:
- ✅ Mock CCXT/SDK 的所有方法
- ✅ 测试参数转换(symbol、side 等)
- ✅ 测试错误处理(各种异常的处理)
- ✅ 测试限频逻辑

### 9.2 集成测试(testnet)

每个交易所都有 testnet,实盘前必须在 testnet 完整跑通:

| 交易所 | Testnet URL |
|---|---|
| Binance | https://testnet.binance.vision |
| Bybit | https://testnet.bybit.com |
| OKX | https://www.okx.com/demo-trading |
| HTX | (无,只能用小额实盘) |
| Bitget | https://www.bitget.com/demo-trading |
| Hyperliquid | https://app.hyperliquid-testnet.xyz |
| dYdX | https://v4.testnet.dydx.exchange |
| Uniswap | Goerli/Sepolia 测试网 |

**集成测试 checklist**:
- ☐ 拉取行情成功
- ☐ 下单 → 部分成交 → 全部成交
- ☐ 下单 → 取消
- ☐ 多笔订单并发
- ☐ WebSocket 持续 24 小时无掉线
- ☐ API 错误情况能正确处理

---

## 十、目录结构

```
exchanges/
├── __init__.py
├── base.py                       # ExchangeAdapter 抽象基类
├── models.py                     # 统一数据模型
├── errors.py                     # 错误类型
├── rate_limiter.py               # 限频
├── connection_pool.py            # 连接池

├── cex/
│   ├── __init__.py
│   ├── ccxt_base.py              # 共享的 CCXT 包装
│   ├── binance.py
│   ├── bybit.py
│   ├── okx.py
│   ├── htx.py
│   └── bitget.py

├── dex_perp/
│   ├── __init__.py
│   ├── hyperliquid.py
│   └── dydx.py

├── dex_spot/
│   ├── __init__.py
│   ├── uniswap_v3.py
│   └── pancakeswap_v3.py

├── websocket/
│   ├── __init__.py
│   ├── ws_manager.py             # 统一的 WS 管理(重连、心跳)
│   ├── binance_ws.py
│   ├── bybit_ws.py
│   ├── okx_ws.py
│   ├── htx_ws.py
│   ├── bitget_ws.py
│   ├── hyperliquid_ws.py
│   └── dydx_ws.py

└── tests/
    ├── unit/
    └── integration/
```

---

## 十一、总结

5 句话:

1. **统一接口** `ExchangeAdapter`,所有策略只调这个,不跟具体交易所打交道
2. **CEX 用 CCXT**(REST)+ 自写 WebSocket
3. **DEX 永续用各自的官方 SDK**(Hyperliquid / dYdX v4)
4. **DEX 现货只读**(Uniswap / Pancake 用 web3.py 模拟 swap 价格)
5. **错误分类清晰**,严重错误立即触发风控熔断

---

## 你的下一步

1. 接受 9 家交易所一起接入吗?或者你想先减少几家(比如 Phase 0 只接 Binance + Bybit + Hyperliquid)?
2. 接下来第 8 篇:数据库 schema 设计
