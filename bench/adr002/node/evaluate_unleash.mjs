import { startUnleash } from "unleash-client";

const shopCount = 10000;
const shopIds = Array.from(
  { length: shopCount },
  (_, i) => `shop-${String(i + 1).padStart(5, "0")}`
);

const unleash = await startUnleash({
  url: "http://localhost:4242/api/",
  appName: "medora-bench-node",
  environment: "development",
  customHeaders: {
    Authorization: "default:development.medora-client-token-secret"
  }
});

const variants = shopIds.map((sid) => {
  const v = unleash.getVariant("adr002-aa", { userId: sid });
  return v.name;
});

unleash.destroy();
console.log(JSON.stringify(variants));
