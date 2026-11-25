const d3=window.d3;
const regional=d3.select('body').attr('data-page')==='regions';
const scenarios=[{id:'ssp585',label:'很高排放的未来',code:'SSP5-8.5',color:'#b64035'},{id:'ssp370',label:'高排放的未来',code:'SSP3-7.0',color:'#c56d37'},{id:'ssp245',label:'中等排放的未来',code:'SSP2-4.5',color:'#a1893a'},{id:'ssp126',label:'低排放的未来',code:'SSP1-2.6',color:'#087e83'}];
const stories=[
 {id:'california',name:'加州',points:[[-120,38]],box:[-125,-114,32,42],copy:'Lorem ipsum dolor sit amet, consectetur adipiscing elit. Sed do eiusmod tempor incididunt ut labore et dolore magna aliqua.'},
 {id:'india',name:'印度',points:[[78,23]],box:[68,89,7,35],copy:'Lorem ipsum dolor sit amet, consectetur adipiscing elit. Sed do eiusmod tempor incididunt ut labore et dolore magna aliqua.'},
 {id:'china',name:'中国',points:[[111.5,30.5]],box:[100,122,24,35],copy:'Lorem ipsum dolor sit amet, consectetur adipiscing elit. Sed do eiusmod tempor incididunt ut labore et dolore magna aliqua.'},
 {id:'amazon',name:'亚马逊',points:[[-60,-4]],box:[-74,-50,-15,5],copy:'Lorem ipsum dolor sit amet, consectetur adipiscing elit. Sed do eiusmod tempor incididunt ut labore et dolore magna aliqua.'},
 {id:'southeast',name:'东南亚沿海',points:[[105.78,10.03],[125,11.25]],box:[102,127,8,22],copy:'Lorem ipsum dolor sit amet, consectetur adipiscing elit. Sed do eiusmod tempor incididunt ut labore et dolore magna aliqua.'}
];
const macro=[{id:'north_america',name:'北美',box:[-170,-50,15,75]},{id:'south_america',name:'南美',box:[-82,-34,-56,14]},{id:'europe',name:'欧洲',box:[-15,40,35,72]},{id:'north_africa',name:'北非',box:[-18,40,15,35]},{id:'sub_saharan',name:'撒哈拉以南非洲',box:[-18,52,-35,15]},{id:'west_asia',name:'西亚',box:[40,65,12,50]},{id:'south_asia',name:'南亚',box:[65,90,5,35]},{id:'east_asia',name:'东亚',box:[90,145,20,55]},{id:'southeast_asia',name:'东南亚',box:[90,145,-10,20]},{id:'oceania',name:'澳洲及周边',box:[110,180,-50,-10]}];
const indicators = {
  temperature: {
    name: '气温', unit: '°C', precision: 1, spatialLabel: '全球',
    palette: t => d3.interpolateRdBu(1 - t),
    signs: ['偏冷', '偏暖'],
    note: '气温变化覆盖陆地和海洋。',
  },
  seaLevel: {
    name: '动态海平面', unit: 'cm', precision: 1,
    palette: t => d3.interpolatePuOr(1 - t),
    signs: ['较低', '较高'],
    note: '模型海表高度（zos）。完整的沿海海平面预测还需加入热膨胀、陆地冰融化和陆地升降等分量。',
  },
  soil: {
    name: '表层土壤水', unit: 'kg/m²', precision: 1, spatialLabel: '陆地',
    palette: d3.interpolateBrBG,
    signs: ['较干', '较湿'],
    note: '显示有效陆地格点的表层土壤水变化。',
  },
};

let dataset = null;
let current = 'ssp585';
let selected = 'california';
let birth = 2004;
let year = 2070;
let indicator = 'temperature';
let activeGrid = null;
let cellNodes = null;
let displayValues = [];
const scales = new Map();

const map = d3.select('#map');
const legend = d3.select('#legend');
const gap = d3.select('#gap');
const projection = d3.geoNaturalEarth1().fitExtent([[10, 8], [890, 468]], {type: 'Sphere'});
const path = d3.geoPath(projection);
const sphere = {type: 'Sphere'};

map.append('path').datum(sphere).attr('d', path).attr('fill', '#e7efef');
map.append('path').datum(d3.geoGraticule10()).attr('class', 'graticule').attr('d', path);
const definitions = map.append('defs');
const landMask = definitions.append('mask').attr('id', 'mask-land')
  .attr('maskUnits', 'userSpaceOnUse').attr('x', 0).attr('y', 0).attr('width', 900).attr('height', 480);
const oceanMask = definitions.append('mask').attr('id', 'mask-ocean')
  .attr('maskUnits', 'userSpaceOnUse').attr('x', 0).attr('y', 0).attr('width', 900).attr('height', 480);
oceanMask.append('path').datum(sphere).attr('d', path).attr('fill', 'white');
const countries = map.append('g');
const cells = map.append('g');
const shores = map.append('g').attr('class', 'shorelines').attr('aria-hidden', 'true');
const markers = map.append('g');
let hasBoundaries = false;

const valid = Number.isFinite;
const currentIndicator = () => indicators[indicator];
const differenceMode = () => d3.select('#mode').property('value') === 'avoided';

function frame(id, displayYear = year) {
  const run = dataset?.scenarios[id];
  if (!run?.years.length) return null;
  const index = d3.leastIndex(run.years, value => Math.abs(value - displayYear));
  return run.frames[index];
}

function metric(record, key = indicator) {
  return key === 'temperature' ? record : record?.indicators?.[key];
}

function compare(value, high) {
  if (!valid(value) || !valid(high)) return null;
  return indicator === 'temperature' ? high - value : value - high;
}

function mapValues(record, key, difference) {
  if (!record) return [];
  if (key === 'temperature') return (difference ? record.avoidedWarming : record.grid) ?? [];
  const layer = metric(record, key);
  return (difference ? layer?.differenceFromHigh : layer?.grid) ?? [];
}

function fmt(value, signed = true) {
  const {precision, unit} = currentIndicator();
  if (!valid(value)) return '无数据';
  const rounded = Math.abs(value) < 0.5 * 10 ** -precision ? 0 : value;
  return `${d3.format(`${signed && rounded !== 0 ? '+' : ''}.${precision}~f`)(rounded)} ${unit}`;
}

function regionSpread(regions) {
  const validValues = Object.values(regions ?? {}).filter(valid);
  return validValues.length >= 2 ? d3.max(validValues) - d3.min(validValues) : null;
}

function signLabels(difference) {
  if (!difference) return currentIndicator().signs;
  return indicator === 'temperature' ? ['多升温', '少升温'] : currentIndicator().signs;
}

function colorDomain(key, difference) {
  const id = `${key}/${difference}`;
  if (!scales.has(id)) {
    // Sample across every scenario and year so scroll and age don't rescale the colors.
    const sampled = [];
    for (const run of Object.values(dataset?.scenarios ?? {})) {
      for (const record of run.frames) {
        const values = mapValues(record, key, difference);
        for (let i = 0; i < values.length; i += 11) {
          if (valid(values[i])) sampled.push(Math.abs(values[i]));
        }
      }
    }
    const limit = Math.max(key === 'temperature' ? 0.5 : 1, d3.quantile(sampled, .98) ?? 0);
    const magnitude = 10 ** Math.floor(Math.log10(limit));
    scales.set(id, Math.ceil(limit / (magnitude / 2)) * (magnitude / 2));
  }
  const bound = scales.get(id);
  return [-bound, 0, bound];
}

function axisDomain(key) {
  const id = `axis/${key}`;
  if (!scales.has(id)) {
    let lo = 0;
    let hi = 0;
    for (const run of Object.values(dataset?.scenarios ?? {})) {
      for (const record of run.frames) {
        const values = Object.values(metric(record, key)?.regions ?? {}).filter(valid);
        if (values.length) {
          lo = Math.min(lo, ...values);
          hi = Math.max(hi, ...values);
        }
      }
    }
    scales.set(id, lo === hi ? [-1, 1] : d3.scaleLinear().domain([lo, hi]).nice(5).domain());
  }
  return scales.get(id);
}

function cellPolygon(lon, lat, dx, dy) {
  const west = lon - dx / 2;
  const east = lon + dx / 2;
  const south = Math.max(-90, lat - dy / 2);
  const north = Math.min(90, lat + dy / 2);
  return {type: 'Polygon', coordinates: [[[west, south], [west, north], [east, north], [east, south], [west, south]]]};
}

function useGrid(id) {
  if (activeGrid === id || !dataset) return;
  activeGrid = id;
  const grid = dataset.grids?.[id] ?? (id === 'atmosphere' ? dataset : null);
  if (!grid) {
    cells.selectAll('path').remove();
    cellNodes = null;
    return;
  }
  const dx = Math.abs(grid.lon[1] - grid.lon[0]);
  const dy = Math.abs(grid.lat[1] - grid.lat[0]);
  const geometry = grid.lat.flatMap((lat, iy) => grid.lon.map((lon, ix) => ({
    index: iy * grid.lon.length + ix,
    shape: cellPolygon(lon, lat, dx, dy),
  })));
  cellNodes = cells.selectAll('path').data(geometry).join('path')
    .attr('d', d => path(d.shape)).attr('fill', 'transparent')
    .on('pointermove', (event, cell) => {
      const value = displayValues[cell.index];
      d3.select('#tooltip')
        .style('display', 'block')
        .style('left', `${Math.max(0, Math.min(event.clientX + 12, innerWidth - 240))}px`)
        .style('top', `${Math.min(event.clientY + 12, innerHeight - 50)}px`)
        .text(`${year} 年 · ${currentIndicator().name} ${fmt(value)}`);
    })
    .on('pointerleave', hideTooltip);
}

function hideTooltip() {
  d3.select('#tooltip').style('display', 'none');
}

function setBirth(value) {
  birth = Math.max(1920, Math.min(2026, Math.round(Number(value) || 2004)));
  d3.selectAll('#birth,#birth-top').property('value', birth);
  const min = Math.max(0, 2015 - birth);
  const max = 2100 - birth;
  year = Math.max(birth + min, Math.min(2100, year));
  d3.select('#age').attr('min', min).attr('max', max).property('value', year - birth);
  render();
}

function selectRegion(id) {
  selected = id;
  markers.selectAll('.marker').classed('active', d => d.id === id);
  d3.selectAll('#region-buttons button').classed('active', d => d.id === id).attr('aria-pressed', d => d.id === id);
  renderStory();
}

d3.selectAll('#birth,#birth-top').on('change', event => setBirth(event.target.value));
d3.select('#age').on('input', event => { year = birth + Number(event.target.value); render(); });
d3.select('#region-year').on('input', event => { year = Number(event.target.value); render(); });
d3.select('#scenario-select').on('change', event => { current = event.target.value; render(); });
d3.select('#indicator').on('change', event => { indicator = event.target.value; render(); });
d3.select('#mode').on('change', render);
d3.select('#begin').on('click', () => d3.select('#experience').node().scrollIntoView({
  behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth',
}));

d3.select('#region-buttons').selectAll('button').data(stories).join('button')
  .text(d => d.name).on('click', (_, d) => selectRegion(d.id));
const pins = (regional ? stories : []).flatMap(story => story.points.map((point, i) => ({
  ...story, point, label: story.id === 'southeast' ? (i ? '菲律宾' : '越南') : story.name,
})));
const pin = markers.selectAll('.marker').data(pins).join('g')
  .attr('class', 'marker').attr('transform', d => `translate(${projection(d.point)})`)
  .attr('tabindex', 0).attr('role', 'button').attr('aria-label', d => `查看${d.label}`)
  .on('click', (_, d) => selectRegion(d.id))
  .on('keydown', (event, d) => {
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      selectRegion(d.id);
    }
  });
pin.append('circle').attr('r', 5);
pin.append('text').attr('x', 9).attr('y', -8).text(d => d.label);

function renderLegend(domain, color, difference) {
  legend.selectAll('*').remove();
  const [low, , high] = domain;
  const x = d3.scaleLinear().domain([low, high]).range([110, 790]);
  legend.selectAll('rect').data(d3.range(80)).join('rect')
    .attr('x', d => 110 + d * 8.5).attr('y', 2).attr('width', 8.6).attr('height', 12)
    .attr('fill', d => color(low + (high - low) * d / 79));
  legend.append('g').attr('class', 'axis').attr('transform', 'translate(0,14)')
    .call(d3.axisBottom(x).ticks(5).tickFormat(value => fmt(value, false)));
  const [negative, positive] = signLabels(difference);
  legend.append('text').attr('x', 110).attr('y', 50).text(negative);
  legend.append('text').attr('x', 790).attr('y', 50).attr('text-anchor', 'end').text(positive);
}

function renderStory() {
  if (!regional) return;
  const story = stories.find(value => value.id === selected);
  const value = metric(frame(current))?.stories?.[selected];
  const high = metric(frame('ssp585'))?.stories?.[selected];
  const card = d3.select('#region-story');
  card.selectAll('*').remove();
  card.append('h3').text(story.name);
  card.append('p').attr('class', 'numbers').text(valid(value)
    ? `${currentIndicator().name}：${fmt(value)} · 相对 SSP5-8.5${indicator === 'temperature' ? ' 少升温量' : ''} ${fmt(compare(value, high))}`
    : '这一代表区域没有该指标的有效格点。');
  card.append('p').text(story.copy);
}

function renderGap() {
  gap.selectAll('*').remove();
  const record = metric(frame(current));
  const reference = metric(frame('ssp585'));
  const rows = macro.map(r => ({...r, value: record?.regions?.[r.id], high: reference?.regions?.[r.id]}))
    .filter(r => valid(r.value) && valid(r.high));
  d3.select('#gap-heading').text(`各地区的${currentIndicator().name}变化`);
  const summary = d3.select('#gap-summary');
  if (!rows.length) {
    summary.text('没有可比较的区域数据。');
    return;
  }
  const x = d3.scaleLinear().domain(axisDomain(indicator)).range([145, 375]);
  const y = d3.scalePoint().domain(rows.map(r => r.id)).range([20, 345]);
  gap.append('g').attr('class', 'axis').attr('transform', 'translate(0,370)')
    .call(d3.axisBottom(x).ticks(4).tickFormat(v => fmt(v, false)));
  gap.append('line').attr('class', 'zero-line').attr('x1', x(0)).attr('x2', x(0)).attr('y1', 4).attr('y2', 356);
  const row = gap.selectAll('.row').data(rows).join('g').attr('class', 'row')
    .attr('transform', d => `translate(0,${y(d.id)})`);
  row.append('text').attr('x', 133).attr('y', 4).attr('text-anchor', 'end').text(d => d.name);
  row.append('line').attr('x1', d => x(d.value)).attr('x2', d => x(d.high)).attr('stroke', '#acc7c0').attr('stroke-width', 3);
  row.append('circle').attr('cx', d => x(d.high)).attr('r', 5).attr('fill', 'none').attr('stroke', '#758382').attr('stroke-width', 2);
  row.append('circle').attr('cx', d => x(d.value)).attr('r', 4).attr('fill', scenarios.find(s => s.id === current).color);
  row.append('title').text(d => `${d.name}：当前 ${fmt(d.value)}，SSP5-8.5 ${fmt(d.high)}`);
  const spread = d3.max(rows, r => r.value) - d3.min(rows, r => r.value);
  summary.text(rows.length > 1 ? `地区最大差距 ${fmt(spread, false)}` : '只有一个地区有有效数据。');
}

function render() {
  const scenario = scenarios.find(s => s.id === current);
  const record = frame(current);
  const selectedMetric = metric(record);
  const high = metric(frame('ssp585'));
  const difference = differenceMode();
  const config = currentIndicator();
  const gridId = dataset?.indicators?.[indicator]?.grid ?? (indicator === 'seaLevel' ? 'ocean' : 'atmosphere');

  if (dataset) useGrid(gridId);
  hideTooltip();
  d3.select('#year').text(year);
  d3.select('#age-label').text(year - birth);
  d3.select('#scenario-code').text(scenario.code);
  d3.select('#scenario-title').text(scenario.label);
  d3.select('#metric-label').text(config.spatialLabel
    ? `${config.spatialLabel}平均${config.name}${difference ? (indicator === 'temperature' ? '少升温量' : '相对 SSP5-8.5 的差值') : '变化'}`
    : '各海区平均变化的最大差距');
  const headline = config.spatialLabel
    ? (difference ? compare(selectedMetric?.global, high?.global) : selectedMetric?.global)
    : regionSpread(selectedMetric?.regions);
  d3.select('#global-value').text(fmt(headline, Boolean(config.spatialLabel)));

  displayValues = mapValues(record, indicator, difference);
  const domain = colorDomain(indicator, difference);
  const palette = difference && indicator === 'temperature' ? d3.interpolateBrBG : config.palette;
  const color = d3.scaleDiverging(palette).domain(domain).clamp(true);
  const clip = indicator === 'soil' ? 'land' : indicator === 'seaLevel' ? 'ocean' : null;
  cells.attr('mask', hasBoundaries && clip ? `url(#mask-${clip})` : null);
  if (cellNodes) {
    cellNodes.interrupt().transition().duration(matchMedia('(prefers-reduced-motion: reduce)').matches ? 0 : 350)
      .attr('fill', d => valid(displayValues[d.index]) ? color(displayValues[d.index]) : 'transparent')
      .attr('pointer-events', d => valid(displayValues[d.index]) ? 'visiblePainted' : 'none');
  }
  map.attr('aria-label', `${config.name}变化地图`);
  d3.select('#baseline').text(dataset && selectedMetric
    ? `${difference ? (indicator === 'temperature' ? 'SSP5-8.5 减当前情景。' : '当前情景减 SSP5-8.5。') : '共同基准：SSP5-8.5 的 2015–2034 年。'} 居中十年平均，端点窗口缩短。色阶跨情景固定，极端值使用端点色。 ${config.note}`
    : '该指标的数据尚未载入。');
  renderLegend(domain, color, difference);
  renderGap();
  renderStory();
}

if (!regional) {
  let queued = false;
  function chooseStep() {
    queued = false;
    const target = innerHeight * .65;
    const nodes = d3.selectAll('.step').nodes();
    if (!nodes.length) return;
    const closest = d3.least(nodes, node => Math.abs(node.getBoundingClientRect().top - target));
    const next = closest.dataset.scenario;
    if (next !== current) { current = next; render(); }
  }
  d3.select(window).on('scroll.scenario', () => {
    if (queued) return;
    queued = true;
    d3.timeout(chooseStep, 16);
  });
}

function loadData(data) {
  const atmosphereSize = data.lat?.length * data.lon?.length;
  if (!atmosphereSize || !data.scenarios) throw Error('缺少地图坐标或情景数据。');
  for (const scenario of scenarios) {
    const run = data.scenarios[scenario.id];
    if (!run?.years.length || run.frames.length !== run.years.length) {
      throw Error(`缺少情景：${scenario.id}`);
    }
    for (const record of run.frames) {
      if (record.grid.length !== atmosphereSize) throw Error('气温格点与坐标数量不一致。');
      for (const [key, layer] of Object.entries(record.indicators ?? {})) {
        if (!layer) continue;
        const grid = data.grids?.[data.indicators[key].grid];
        if (!grid || layer.grid.length !== grid.lat.length * grid.lon.length) {
          throw Error(`${key} 格点与坐标数量不一致。`);
        }
      }
    }
  }
  dataset = data;
  activeGrid = null;
  scales.clear();
  d3.select('#indicator').selectAll('option').property('disabled', function () {
    const key = this.value;
    return key !== 'temperature' && !Object.values(data.scenarios)
      .some(run => run.frames.some(record => record.indicators?.[key]));
  });
  d3.select('#status').text(`已载入 ${data.source || 'CESM2'}。`);
}

try {
  const world = await d3.json('world.geojson');
  countries.selectAll('path').data(world.features).join('path').attr('class', 'country').attr('d', path);
  shores.selectAll('path').data(world.features).join('path').attr('d', path);
  landMask.selectAll('path').data(world.features).join('path').attr('d', path).attr('fill', 'white');
  oceanMask.append('g').selectAll('path').data(world.features).join('path').attr('d', path).attr('fill', 'black');
  hasBoundaries = true;
} catch (error) {
  console.warn('底图加载失败', error);
}
try {
  loadData(await d3.json('climate.json'));
} catch (error) {
  d3.select('#status').text(`无法读取 climate.json。请运行 python build_json.py --only climate。 ${error.message}`);
}
if (regional) { render(); selectRegion(selected); } else setBirth(birth);
