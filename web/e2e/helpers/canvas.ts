import { expect, type Locator, type Page } from "@playwright/test";

// 画布交互帮助函数：DnD（HTML5 dataTransfer，NodePalette.tsx:167 口径）、
// 连线（reactflow 左入右出手势）、执行状态浮层断言（design D3）。

/**
 * 调色板→画布拖入一个节点类型（合成 dataTransfer + dragover/drop）。
 * x/y 为视口坐标（screenToFlowPosition 消费 clientX/clientY）——
 * 必须显式传参：合成事件缺省 clientX=0 会导致节点全部叠在左上角。
 */
export async function dropNodeType(page: Page, nodeType: string, x = 400, y = 250): Promise<void> {
  const dataTransfer = await page.evaluateHandle((t) => {
    const dt = new DataTransfer();
    dt.setData("application/node-type", t);
    return dt;
  }, nodeType);
  await page.dispatchEvent(".react-flow", "dragover", { dataTransfer, clientX: x, clientY: y });
  await page.dispatchEvent(".react-flow", "drop", { dataTransfer, clientX: x, clientY: y });
}

/** 画布节点定位器。 */
export function canvasNodes(page: Page): Locator {
  return page.locator(".react-flow__node");
}

/** 画布连线定位器。 */
export function canvasEdges(page: Page): Locator {
  return page.locator(".react-flow__edge");
}

/** 节点间连线：源节点右侧 source handle → 目标节点左侧 target handle。
 *  reactflow v11 类名口径：`.react-flow__handle` + 位置类 + 裸类 source/target。 */
export async function connectNodes(page: Page, fromIdx: number, toIdx: number): Promise<void> {
  const src = canvasNodes(page).nth(fromIdx).locator(".react-flow__handle.source");
  const dst = canvasNodes(page).nth(toIdx).locator(".react-flow__handle.target");
  const sb = await src.boundingBox();
  const db = await dst.boundingBox();
  if (!sb || !db) {
    throw new Error("连线手柄 boundingBox 缺失");
  }
  await page.mouse.move(sb.x + sb.width / 2, sb.y + sb.height / 2);
  await page.mouse.down();
  await page.mouse.move(db.x + db.width / 2, db.y + db.height / 2, { steps: 5 });
  await page.mouse.up();
}

/** 执行状态浮层：断言「<状态中文>: <计数>」行可见（NodeExecutionStatusOverlay 口径）。 */
export async function expectOverlayStatus(page: Page, label: string, count: number): Promise<void> {
  await expect(page.getByText("执行状态").first()).toBeVisible();
  await expect(page.getByText(`${label}: ${count}`).first()).toBeVisible({ timeout: 20_000 });
}

/** 清空画布持久化并回到干净首屏（每用例隔离起点）。 */
export async function resetCanvas(page: Page): Promise<void> {
  await page.goto("/");
  await page.evaluate(() => {
    localStorage.clear();
  });
  await page.reload();
}
