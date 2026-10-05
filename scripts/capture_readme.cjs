/* Capture the actual desktop-shared page. Data is seeded by capture_readme.py. */
const assert = require('node:assert/strict');
const path = require('node:path');
const {chromium} = require(process.argv[3] || process.env.READER_PLAYWRIGHT || 'playwright');
const root = path.resolve(__dirname, '..');
const output = path.join(root, 'docs/assets/readme');

async function main() {
  const origin = process.argv[2];
  assert.match(origin || '', /^http:\/\/127\.0\.0\.1:\d+$/);
  const browser = await chromium.launch({channel:'chrome', headless:true});
  try {
    const context = await browser.newContext({viewport:{width:1440,height:1040}, deviceScaleFactor:2,
      locale:'zh-CN', timezoneId:'Asia/Shanghai', colorScheme:'light'});
    await context.route('**/*', route => {
      const target = new URL(route.request().url());
      return target.origin === origin ? route.continue() : route.abort();
    });
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    page.on('response', response => {
      if(response.status() >= 400 && !response.url().endsWith('/favicon.ico')) {
        errors.push(`${response.status()} ${new URL(response.url()).pathname}`);
      }
    });
    await page.goto(origin);
    const intro = page.getByRole('button', {name:'先看看界面',exact:true});
    if(await intro.isVisible()) await intro.click();
    await page.waitForFunction(() => typeof state !== 'undefined' && state.sessions.length === 1 && window.readerConfigured === false);
    await page.evaluate(() => document.fonts.ready);
    const capture = async name => {
      await page.evaluate(() => document.fonts.ready);
      assert.ok(!/\b(?:undefined|NaN)\b/.test(await page.locator('body').innerText()), 'Incomplete screenshot fixture');
      await page.screenshot({path:path.join(output,name),animations:'disabled',fullPage:false});
    };
    await page.locator('#chat-welcome').waitFor({state:'visible'});
    assert.ok(!(await page.locator('.desktop-toolbar').innerText()).includes('Agent 执行'));
    await capture('home.png');

    await page.locator('.ws-nav-item[data-v="library"]').click();
    await page.waitForFunction(() => document.querySelectorAll('#paper-table-body .paper-title').length === 5);
    await page.locator('#library-categories button').filter({hasText:'检索与生成'}).waitFor();
    assert.equal(await page.locator('#library-organize').innerText(), '自动整理');
    await page.locator('#paper-table-body input[type="checkbox"]').nth(0).check();
    await page.locator('#paper-table-body input[type="checkbox"]').nth(1).check();
    await page.locator('#paper-table-body input[type="checkbox"]').nth(2).check();
    await page.locator('#library-selection-bar').waitFor({state:'visible'});
    await capture('library.png');

    await page.locator('.session-open').click();
    await page.waitForFunction(() => document.querySelectorAll('.research-step').length === 4);
    const reading = page.locator('.research-step').filter({hasText:'逐篇阅读与核对证据'});
    await reading.locator('summary').click();
    await reading.locator('.research-prose').first().waitFor({state:'visible'});
    await page.evaluate(() => {document.getElementById('chat-content').scrollTop=0;});
    await capture('conversation.png');

    await page.getByRole('button', {name:'查看完整报告',exact:true}).click();
    await page.locator('#rr-body .report-reading').waitFor();
    assert.ok((await page.locator('#rr-body').innerText()).includes('引用核查反馈驱动的证据排序'));
    assert.ok((await page.locator('#rr-toc').innerText()).includes('方法对照'));
    await capture('report.png');

    await page.locator('#desktop-settings').click();
    await page.locator('.desktop-modal').waitFor();
    assert.equal(await page.locator('#cfg-key').inputValue(), '');
    await capture('settings.png');
    assert.deepEqual(errors, [], 'Current UI did not load cleanly');
    console.log('README_CAPTURE_OK: five images, 2880 × 2080 pixels; no external requests.');
  } finally {
    await browser.close();
  }
}
main().catch(error => {console.error(error);process.exitCode=1;});
