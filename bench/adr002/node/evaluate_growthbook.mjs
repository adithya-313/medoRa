import { GrowthBook } from "@growthbook/growthbook";

const shopCount = 10000;
const shopIds = Array.from(
  { length: shopCount },
  (_, i) => `shop-${String(i + 1).padStart(5, "0")}`
);

const sdkKey = process.argv[2];
const res = await fetch(`http://localhost:3110/api/features/${sdkKey}`);
const data = await res.json();

const gb = new GrowthBook({
  features: data.features
});

const variants = shopIds.map((sid) => {
  gb.setAttributes({ id: sid });
  return gb.getFeatureValue("adr002-aa", "B");
});

console.log(JSON.stringify(variants));
