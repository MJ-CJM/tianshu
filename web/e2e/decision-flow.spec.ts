import {
  createPlanReviewEdict,
  expect,
  seedClosedEvidence,
  test,
  waitForClosedEvidence,
} from "./fixtures";

test("Royal Study keeps unified task controls visible when the workspace is empty", async ({
  isolatedStack,
  page,
}) => {
  await page.goto(`${isolatedStack.baseURL}/approvals`);

  await expect(page.getByRole("heading", { name: "Task Workspace" })).toBeVisible();
  await expect(page.getByPlaceholder("Search tasks...")).toBeVisible();
  // antd 把 aria-label 挂在内部 <input role="combobox"> 上，而当前选中项的文本
  // 渲染在兄弟节点 .ant-select-selection-item 里——对 input 断言 toContainText
  // 拿到的恒是空串（实测 textContent === ""）。故断言其所属的 .ant-select 容器。
  const statusFilter = page.getByRole("combobox", { name: "Status" });
  await expect(statusFilter).toBeVisible();
  await expect(page.locator(".ant-select").filter({ has: statusFilter })).toContainText(
    "All Status",
  );
  await expect(page.getByRole("columnheader", { name: "Task type" })).toBeVisible();
  await expect(page.getByRole("columnheader", { name: "Current progress" })).toBeVisible();
  await expect(page.locator("main .ant-empty")).toBeVisible();

  await page.getByRole("button", { name: "Refresh" }).click();
  await expect(page.locator("main .ant-empty")).toBeVisible();
});

test("pending Decision requires a reason, resolves at the expected version, and downloads closed Evidence", async ({
  stack,
  page,
}) => {
  const seeded = await createPlanReviewEdict(stack);
  await page.goto(`${stack.baseURL}/approvals`);

  await expect(page.getByRole("heading", { name: "Task Workspace" })).toBeVisible();
  // 按 edictId 精确定位本用例 seed 的那一条。此用例用的是共享的 `stack`
  // fixture（区别于上一个用例的 isolatedStack），工作台里可能同时存在同名敕令，
  // 按标题文本匹配会撞 strict mode violation（曾偶发，取决于用例执行顺序）。
  const seededRow = page.locator(`a[href="/edicts/${seeded.edictId}"]`);
  await expect(seededRow).toBeVisible();
  const seededTr = page.getByRole("row").filter({ has: seededRow });
  await expect(seededTr.getByText("Immediate", { exact: true })).toBeVisible();
  await expect(seededTr.getByText("Conversation", { exact: true })).toBeVisible();
  await expect(seededTr.getByText("Pending Decision", { exact: true })).toBeVisible();
  await seededRow.click();
  await expect.poll(() => new URL(page.url()).pathname).toBe(`/edicts/${seeded.edictId}`);
  await page.getByRole("button", { name: /Governance & audit/ }).click();
  await expect(page.getByRole("heading", { name: "Decision" })).toBeVisible();
  await page.getByRole("button", { name: "Submit decision" }).click();
  await expect(page.getByText("A decision reason is required")).toBeVisible();

  await page.getByLabel("Decision reason").fill("S4 browser gate reviewed version 1");
  await page.getByRole("button", { name: "Submit decision" }).click();
  await expect(page.getByText("Resolved", { exact: true })).toBeVisible();
  await expect(page.getByText("S4 browser gate reviewed version 1")).toBeVisible();

  await seedClosedEvidence(stack, seeded.edictId);
  const evidence = await waitForClosedEvidence(stack, seeded.edictId);
  await page.reload();
  await page.getByRole("button", { name: /Governance & audit/ }).click();
  const downloadPromise = page.waitForEvent("download");
  await page.getByRole("link", { name: "Download evidence bundle" }).click();
  const download = await downloadPromise;
  const stream = await download.createReadStream();
  expect(stream).not.toBeNull();
  const chunks: Buffer[] = [];
  for await (const chunk of stream!) chunks.push(Buffer.from(chunk));
  const exported = JSON.parse(Buffer.concat(chunks).toString("utf8")) as {
    bundle_id: string;
    content_hash: string;
  };
  expect(exported).toMatchObject({
    bundle_id: evidence.bundleId,
    content_hash: evidence.contentHash,
  });
});
