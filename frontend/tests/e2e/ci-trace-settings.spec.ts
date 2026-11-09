import { expect, test } from "@playwright/test";

test("only custom providers show a Base URL field", async ({ page }) => {
  await page.route("**/api/ci-diagnoses/model", (route) =>
    route.fulfill({ json: null }),
  );
  await page.goto("/diagnoses/model");
  const provider = page.getByLabel("提供商");
  await provider.selectOption("siliconflow");
  await expect(page.getByLabel("Base URL")).toHaveCount(0);
  await provider.selectOption("custom");
  await expect(page.getByLabel("Base URL")).toBeVisible();
});

test("public diagnosis stays accessible and model settings can be cleared", async ({
  page,
}) => {
  let saved: { provider: string; model: string } | null = null;
  await page.route("**/api/ci-diagnoses/model", async (route) => {
    switch (route.request().method()) {
      case "PUT": {
        const { provider, model } = route.request().postDataJSON();
        saved = { provider, model };
        await route.fulfill({ json: saved });
        break;
      }
      case "DELETE":
        saved = null;
        await route.fulfill({ status: 204 });
        break;
      default:
        await route.fulfill({ json: saved });
    }
  });
  await page.goto("/diagnoses");
  await expect(page.getByRole("button", { name: "开始诊断" })).toBeVisible();
  await page.getByRole("link", { name: "模型设置" }).click();
  await page.getByLabel("模型名").fill("test-model");
  await page.getByLabel("API Key").fill("test-key");
  await page.getByRole("button", { name: "测试并保存" }).click();
  await expect(page.getByText("已配置 · deepseek / test-model")).toBeVisible();
  await expect(page.getByLabel("API Key")).toBeEmpty();
  await page.getByRole("button", { name: "清除配置" }).click();
  await expect(page.getByText("当前使用基础诊断")).toBeVisible();
});
