// Reads your Shkolo e-diary (app.shkolo.bg) in Jarvis's hidden browser, already logged in, and prints it as JSON:
// grades per subject, homework, this week's and next week's timetable, absences. Used by jarvis/shkolo.py.
//
//   node jarvis/shkolo_read.js <browser profile> [file with the grade ids already known]
//
// For grades it hasn't seen before it also fetches the details (date, type, teacher). Nothing is ever changed on
// Shkolo: it only opens pages and reads them.
const fs = require("fs");
const path = require("path");
const ROOT = path.resolve(__dirname, "..");
const { chromium } = require(path.join(ROOT, ".brain", "browser-mcp", "node_modules", "playwright-core"));

const BASE = "https://app.shkolo.bg";
const profile = process.argv[2] || path.join(ROOT, ".brain", "browser-profile");
const known = new Set(process.argv[3] && fs.existsSync(process.argv[3]) ? JSON.parse(fs.readFileSync(process.argv[3], "utf8")) : []);
const tidy = s => (s || "").replace(/\s+/g, " ").trim();
const out = { ok: false };

async function tab(page, name, ready) {
  await page.click(`a[href="#${name}"]`).catch(() => {});
  await page.waitForSelector(ready, { timeout: 15000 }).catch(() => {});
  await page.waitForTimeout(500);
}

function readTimetable() {   // runs in the page: the week on show
  const days = [...document.querySelectorAll("#tab_schedule .scheduleTableColumn")].map(col => {
    const head = (col.querySelector(".scheduleTableHeading .hidden-xs")?.innerText || "").trim();
    const date = ((col.querySelector(".scheduleTableHeading")?.innerText || "").match(/\d{2}\.\d{2}\.\d{4}/) || [""])[0];
    const lessons = [...col.querySelectorAll(".scheduleTableCourse .taken-shi, .scheduleTableCourse > div")].map(c => {
      const a = c.querySelector(".scheduleCourse");
      if (!a) return null;
      const sec = [...c.querySelectorAll(".scheduleSecondary")].map(s => s.innerText.replace(/\s+/g, " ").trim());
      return {
        n: (a.querySelector(".hourNum")?.innerText || "").replace(".", "").trim(),
        subject: a.innerText.replace(/^\s*\d+\.\s*/, "").replace(/\s+/g, " ").trim(),
        teacher: sec[0] || "", room: sec[1] || "", time: sec.find(x => /\d\d:\d\d/.test(x)) || "",
      };
    }).filter(Boolean);
    const note = lessons.length ? "" : (col.querySelector(".scheduleTableBody")?.innerText || "").replace(/\s+/g, " ").trim();
    return { day: head, date, lessons, note };
  });
  const week = (document.querySelector("#tab_schedule .scheduleWeekNum.active")?.innerText || "").trim();
  return { week, days };
}

(async () => {
  let ctx;
  try {
    ctx = await chromium.launchPersistentContext(profile, { channel: "chrome", headless: true, viewport: { width: 1366, height: 900 } });
  } catch (e) {
    out.error = /in use|ProcessSingleton|lock/i.test(e.message) ? "busy" : "no_browser";
    out.detail = e.message.split("\n")[0];
    console.log(JSON.stringify(out));
    return;
  }
  const page = ctx.pages()[0] || await ctx.newPage();
  try {
    await page.goto(BASE + "/dashboard", { waitUntil: "domcontentloaded", timeout: 60000 });
    await page.waitForLoadState("networkidle", { timeout: 20000 }).catch(() => {});
    if (/\/auth|login/i.test(page.url())) { out.error = "logged_out"; return; }

    // whose diary: the pupil the dashboard's stats card is about (this school year's class)
    const pupil = await page.$$eval('a[href*="/diary/pupil/"]', as => {
      const a = as.find(x => /#tab_grades/.test(x.getAttribute("href"))) || as[0];
      if (!a) return null;
      const id = a.getAttribute("href").match(/pupil\/(\d+)/)[1];
      const named = as.find(x => x.getAttribute("href") === `/diary/pupil/${id}` && /[A-Za-zА-Яа-я]{3}/.test(x.innerText));
      return { id, name: named ? named.innerText.replace(/\s+/g, " ").trim() : "" };
    });
    if (!pupil) { out.error = "no_pupil"; return; }
    out.pupil = pupil;
    await page.goto(`${BASE}/diary/pupil/${pupil.id}`, { waitUntil: "domcontentloaded", timeout: 60000 });
    await page.waitForLoadState("networkidle", { timeout: 20000 }).catch(() => {});

    // grades: each subject's current grades per term, the term grades and the year's
    await tab(page, "tab_grades", "#tab_grades tr.compactTableRow");
    out.grades = await page.$$eval("#tab_grades tr.compactTableRow[data-course-id]", rows => rows.map(r => {
      const cells = [...r.querySelectorAll("td")];
      const g = sel => [...r.querySelectorAll(sel + " button.grade")].map(b => ({ id: b.id.replace("grade_", ""), value: b.innerText.trim() }));
      const num = sel => (r.querySelector(sel)?.innerText || "").trim();
      return {
        subject: (cells[1]?.innerText || "").replace(/\s+/g, " ").trim(),
        term1: g("td.term1:not(.numVal)"), term1_final: num("td.numVal.term1"),
        term2: g("td.term2:not(.numVal)"), term2_final: num("td.numVal.term2"),
        year_final: num("td.annualAssessment"),
      };
    }));
    out.average = await page.$$eval("#tab_grades tr", rows => {
      const r = rows.find(x => /Средноаритметично/.test(x.innerText));
      return r ? (r.innerText.match(/\d+[.,]\d+/) || [""])[0] : "";
    });

    // details (date, type, teacher) for grades not seen before
    const fresh = out.grades.flatMap(s => [...s.term1, ...s.term2]).filter(x => !known.has(x.id)).slice(0, 40);
    out.details = {};
    for (const x of fresh) {
      const html = await page.evaluate(async id => (await fetch(`/ajax/diary/getGradeDataById/${id}?new_grade_id=`,
        { headers: { "X-Requested-With": "XMLHttpRequest" } })).text(), x.id).catch(() => "");
      const t = tidy(html.replace(/<[^>]+>/g, " "));
      out.details[x.id] = {
        date: (t.match(/(\d{2}\.\d{2}\.\d{4})/) || [""])[1],
        type: (t.match(/Тип\s*:\s*(.+?)\s+Предмет/) || ["", ""])[1],
        teacher: (t.match(/Въведена от\s*:\s*(.+?)\s*\//) || ["", ""])[1],
      };
    }

    // homework: every subject that has any, newest first
    await tab(page, "tab_homeworks", "#tab_homeworks a.coursesNav");
    const subjects = await page.$$eval("#tab_homeworks a.coursesNav", as => as.map((a, i) => ({
      i, title: a.getAttribute("title") || a.innerText, count: +((a.querySelector(".badge")?.innerText || "0").trim()) || 0 })));
    out.homework = [];
    for (const s of subjects.filter(x => x.count > 0)) {
      await page.$$eval("#tab_homeworks a.coursesNav", (as, i) => as[i].click(), s.i);
      await page.waitForTimeout(1500);
      const m = s.title.match(/^(.*?)\s*\(([^()]*)\)\s*$/) || [null, s.title, ""];
      const rows = await page.$$eval("#tab_homeworks table tbody tr", trs => trs.map(tr => {
        const td = [...tr.querySelectorAll("td")].map(x => x.innerText.replace(/\s+/g, " ").trim());
        const a = tr.querySelector('a[href*="/homework/"]');
        return td.length >= 5 ? { date: td[1], text: td[2], file: td[3], due: td[4], id: a ? a.getAttribute("href").match(/homework\/(\d+)/)[1] : "" } : null;
      }).filter(Boolean));
      for (const r of rows) out.homework.push({ subject: tidy(m[1]), teacher: tidy(m[2]), ...r });
    }

    // timetable: this week, and next week (for "tomorrow" on a Friday)
    await tab(page, "tab_schedule", "#tab_schedule .scheduleTableColumn");
    out.timetable = [await page.evaluate(readTimetable)];
    const next = await page.$eval("#tab_schedule .weekLi.active + .weekLi a.scheduleWeekNum", a => a.dataset.week).catch(() => "");
    if (next) {
      await page.click(`#tab_schedule a.scheduleWeekNum[data-week="${next}"]`).catch(() => {});
      await page.waitForFunction(w => document.querySelector("#tab_schedule .scheduleWeekNum.active")?.dataset.week === w, next, { timeout: 15000 }).catch(() => {});
      await page.waitForTimeout(800);
      out.timetable.push(await page.evaluate(readTimetable));
    }

    // absences: totals per subject that has any
    await tab(page, "tab_absences", "#tab_absences table");
    out.absences = await page.$$eval("#tab_absences table tr", trs => trs.map(tr => {
      const td = [...tr.querySelectorAll("td")].map(x => x.innerText.replace(/\s+/g, " ").trim());
      return td.length >= 6 && /^\d+$/.test(td[0]) ? { subject: td[1], late: td[2], unexcused: td[4], excused: td[5] } : null;
    }).filter(x => x && (+x.late || +x.unexcused || +x.excused)));
    out.absence_total = await page.$eval('a[href="#tab_absences"]', a => (a.innerText.match(/\d+(?:[.,]\d+)?/) || [""])[0]).catch(() => "");
    out.ok = true;
  } catch (e) {
    out.error = "failed";
    out.detail = e.message.split("\n")[0];
  } finally {
    console.log(JSON.stringify(out));
    await ctx.close().catch(() => {});
  }
})();
