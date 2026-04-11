import fs from 'fs';
import { parseSync, stringify } from 'svgson';

const svgStr = fs.readFileSync('/Users/alexandersibast/Documents/Financesum/svg finance icons/764207_03.svg', 'utf-8');
const parsed = parseSync(svgStr);

const objectsGroup = parsed.children.find(c => c.name === 'g' && c.attributes.id === 'Objects');

const items = [];
objectsGroup.children.forEach(child => {
  if (child.name === 'g' || child.name === 'path') {
    items.push(child);
  }
});
items.forEach((item, idx) => {
  const newSvg = {
    name: 'svg',
    type: 'element',
    value: '',
    attributes: {
      xmlns: "http://www.w3.org/2000/svg",
      viewBox: "0 0 800 800"
    },
    children: [item]
  };
  fs.writeFileSync(`/Users/alexandersibast/Documents/Financesum/frontend/public/finance_icon_${idx}.svg`, stringify(newSvg));
  console.log(`Saved finance_icon_${idx}.svg`);
});
