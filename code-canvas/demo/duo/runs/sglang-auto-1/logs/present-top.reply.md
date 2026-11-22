## 讲解员报告（顶层）

**1. 画了什么、为什么这么取舍**

- **8 个框，按三个进程分三组虚线框。**
  - 主进程：客户端、聊天协议层、请求登记处（TokenizerManager）、收件协程（handle_loop）。
  - Scheduler 子进程：调度循环、模型前向 + 采样、KV 缓存 + 前缀树。
  - Detokenizer 子进程：反分词。
- **11 条线。** 进程之间的 ①②③ 三条单向管道是主干；其余是进程内交接（POST、input_ids、投信箱·叫醒、yield 全文、SSE 块）和 Scheduler ↔ GPU、Scheduler ↔ KV。
  - 「token 晚一拍」画成虚线，用来表现 overlap：结果下一圈才处理。
- **没画成框的，以及原因：**
  - **ReqState 没有单独成框**，画成 TokenizerManager 框里的小图（rid / out_list / 状态在推演中变化）。它是 TokenizerManager 里的一份数据，不是独立部件。
  - **收件协程（handle_loop）单独成框**，因为「发请求和收结果是两个协程」是底稿点名最容易画错的地方。
  - **等待队列、running batch、future_map 只在说明卡和推演里出现。** 它们属于调度循环内部，留给它的下一层。
  - **旁路（/generate、abort 细节、多卡、推测解码等）不画**，只在卡里提一句。
- **下一层的分法：**
  - 直接挂源码页（4 页），因为里面基本是一段能从头读到尾的代码：
    - `src_chat`：handle_request / 踢一脚 / 切增量
    - `src_tm`：generate_request / ReqState / _stream_one_response
    - `src_hloop`：handle_loop / text=None / 攒着叫醒
    - `src_detok`：event_loop / DetokenizeState / step
  - 申请 3 个槽位（`dives.json`），因为里面有一整套机制，底稿只讲到高层：
    1. `sched_loop` 调度循环（overlap、future_map、选批、白算一步）
    2. `kv_cache` KV 缓存与前缀树
    3. `model_fwd` 模型前向 + 采样
  - 模型前向本来也可以直接挂源码，但 `forward_batch_generation` 里夹着大量旁路分支，直接贴给读者会很乱，所以改成申请下一层。
- **推演例子**：用底稿 §8 那条「法国首都是哪里？」，共 12 步，数值一路一致（prompt 20 token、命中 5、新分 15、rid 9f3c…、X1→「巴」、X2 半个字节憋住、X3「黎」、X4「。」、X5=EOS 白算一步并写进前缀树、最后 [DONE]）。各框的小图会随着推演步骤换成新值。

**2. 最终数字**
- 构建 WARN 0 条，排版问题 0 条（桌面 / 手机、三个图层、12 步都量过）。
- `--all`：71 PASS / 2 FAIL。这 2 条 FAIL 都是「钻进去指到未展开页」，对应的就是 3 个申请中的槽位（sched_loop / model_fwd / kv_cache），属于预期内。4 个源码页各自 PASS。
- 截图用了 3 轮：第 1 轮顶层，第 2、3 轮各一次 `--all`。

**3. 自己看图觉得还不够好的地方**
- 手机上图很长（约 1560 高）。① 和「匹配 / 分槽」两条线走右侧窄通道，标签在「为什么 / 易混点」图层下会折成两三行，读起来有点挤。
- 推演开始前，TokenizerManager、反分词、GPU 三个框的小图是「—」占位，初看会显得空。
- 「发射和处理差一圈」是全图最难懂的概念，在顶层只能靠第 6 步和第 11 步的文字讲；真正画清楚要等 `sched_loop` 那一层。

**4. 推断与不确定之处**
- **推断（沿用底稿的推断，或我自己补的设计理由）：**
  - 为什么拆成三个进程、为什么 prefill 优先、为什么 Scheduler 收请求不阻塞（都来自底稿的推断）。
  - 为什么用「Event + list」做信箱（底稿推断）。
  - 「全文 → 再切成增量」是为了兼容原生接口契约（底稿推断）。
  - 不能单独解新 token 的原因（底稿陈述但没标来源，我标了推断）。
  - 几条线卡上的设计理由是我自己写的推断，比如批量过管道、在最后一站按偏移切。
- **不够确定的事实**：SSE 块的具体字段、聊天层怎么处理空增量，底稿自己也说没核实，我在卡里照实写了。
- **底稿缺的事实**：没有必须补的。

**5. 舞台问题**（已写入 `content/framework-issues-scene.md`）
- **证据锚点不认 `.mdx`**：引 `unified_memory_design.mdx:121` 会直接 BuildError，整页退回临时骨架。我已改成引 `schedule.py:215` 的配置说明绕开。
- **「为什么」图层框底那句的等级小标取自卡里第一条 note**：如果第一条 note 的等级和框底那句话对不上，就会显示错的等级。这一点 SCENE.md 没有写明；我已靠调整 note 顺序解决。
- **想做但工具不支持的表达**：一条线上画出「同一时刻 GPU 在算 N+1、CPU 在处理 N」的时间并行，现在只能靠文字和虚线。

文件都在 `/tmp/claude-0/-home-user-code-canvas/f73966bb-7be3-5269-bc4d-ca2eebd0fcae/scratchpad/runs/sglang-auto-1/content/`：
- content_scene.py
- dives.json
- framework-issues-scene.md
