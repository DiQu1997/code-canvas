# Code Canvas 交接包

这是 Code Canvas 项目的完整交接包——把它整个放进一个新仓库的根目录即可接手。

## 给下一个 Claude session 的启动指令（复制粘贴用）

> 这个仓库是 Code Canvas 项目（AI 时代的代码控制面板）。按顺序读：
> AGENTS.md（工作规程与环境）→ ROADMAP.md（计划、验收标准、当前进度）
> → HANDOFF.md（完整认知：定位、设计理由、被否方案、协作方式）。
> 然后跑 AGENTS.md 里的环境自检。确认全绿后告诉我你对项目的理解和
> 待办池的下一项，等我确认后开工。

## 包内容

- `HANDOFF.md` — 交接书（先读这个）
- `code-canvas/` — 全部代码：skill 定义、schema、渲染器、验证器、
  问答服务、评测框架、测试、三张 demo、手工原型
- `examples/` — 质量基准：两份零上下文考卷的产物（codex 领航图 9 卡 /
  codex 沙箱深潜 15 卡）、考生原始报告、关键截图。这是"合格线"的实物定义

## 快速验证（30 秒）

```bash
cd code-canvas
python3 validate.py demo/nano-vllm.json && python3 render.py demo/nano-vllm.json /tmp/canvas.html
# 浏览器打开 /tmp/canvas.html，按 ▶ 走 6 步
```
