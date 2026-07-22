import {
  createPlanReviewEdict,
  expect,
  seedClosedEvidence,
  test,
  waitForClosedEvidence,
} from "./fixtures";

test("pending Decision requires a reason, resolves at the expected version, and downloads closed Evidence", async ({
  stack,
  page,
}) => {
  const seeded = await createPlanReviewEdict(stack);
  await page.goto(`${stack.baseURL}/edicts/${seeded.edictId}`);

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
