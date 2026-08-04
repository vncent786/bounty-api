"""Inspect TikTok Send code DOM after filling signup form."""
import asyncio
import os
import sys
sys.path.insert(0, '.')
from account_factory.tiktok_creator import TikTokSignupBot, generate_identity

async def main():
    identity = generate_identity()
    email = os.environ.get('TEST_EMAIL', 'debuguser12345@gmail.com')
    bot = TikTokSignupBot(headless=True)
    await bot.launch()
    page = bot.page
    try:
        await page.goto('https://www.tiktok.com/signup', wait_until='networkidle', timeout=30000)
        await asyncio.sleep(2)
        for el in await page.query_selector_all('[data-e2e="channel-item"]'):
            txt = (await el.inner_text()).strip().lower()
            if 'phone or email' in txt:
                await el.click(); break
        await asyncio.sleep(2)
        await page.get_by_text('Sign up with email', exact=True).click()
        await asyncio.sleep(1)
        await bot._select_birthday(identity['birth_month'], identity['birth_day'], identity['birth_year'])
        await asyncio.sleep(1)
        # Fill email and password fast
        inputs = await page.query_selector_all('input')
        for inp in inputs:
            ph = (await inp.get_attribute('placeholder') or '').lower()
            typ = (await inp.get_attribute('type') or '').lower()
            if 'email' in ph:
                await inp.fill(email)
            if typ == 'password' or 'password' in ph:
                await inp.fill(identity['password'])
        await page.keyboard.press('Escape')
        await asyncio.sleep(1)
        await page.screenshot(path='tiktok_screenshots/dom_inspect.png')

        print('URL', page.url)
        print('BODY', (await page.inner_text('body'))[:1000])

        # Dump all elements with send/code text or near code input
        data = await page.evaluate('''() => {
          function box(el){ const r=el.getBoundingClientRect(); return {x:r.x,y:r.y,w:r.width,h:r.height}; }
          function path(el){ let p=[]; while(el && p.length<5){ p.push(el.tagName+'#'+(el.id||'')+'.'+(el.className||'').toString().slice(0,80)); el=el.parentElement;} return p.join(' < '); }
          const out=[];
          const all=[...document.querySelectorAll('*')];
          for (const el of all) {
            const txt=(el.innerText||el.textContent||'').trim();
            const aria=el.getAttribute('aria-label')||'';
            const role=el.getAttribute('role')||'';
            const de=el.getAttribute('data-e2e')||'';
            const cls=(el.className||'').toString();
            const r=el.getBoundingClientRect();
            if (!r.width || !r.height) continue;
            if (/send code|enter 6-digit|code/i.test(txt+aria+de) || (r.y>380 && r.y<540 && r.x>350 && r.x<900)) {
              out.push({tag:el.tagName, text:txt.slice(0,120), aria, role, dataE2E:de, cls:cls.slice(0,160), box:box(el), path:path(el), disabled:el.disabled||el.getAttribute('disabled')});
            }
          }
          return out;
        }''')
        import json
        print(json.dumps(data, indent=2))

        # Try clicking every send-code-ish element and observe body after each.
        handles = await page.query_selector_all('*')
        idx = 0
        for h in handles:
            try:
                txt = ((await h.inner_text()) or '').strip()
            except Exception:
                continue
            if txt.lower() == 'send code':
                idx += 1
                print('TRY_CLICK', idx, txt, await h.evaluate('el => el.tagName + " " + (el.className||"")'))
                try:
                    await h.click(timeout=3000, force=True)
                except Exception as e:
                    print('click err', e)
                await asyncio.sleep(2)
                await page.screenshot(path=f'tiktok_screenshots/dom_click_{idx}.png')
                print('AFTER', (await page.inner_text('body'))[:500])
    finally:
        await bot.close()

if __name__ == '__main__':
    asyncio.run(main())
