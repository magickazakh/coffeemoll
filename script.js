const tg = window.Telegram.WebApp;
tg.ready();
tg.expand(); 

// === НАСТРОЙКИ ===
const RAW_SHEET_URL = "https://docs.google.com/spreadsheets/d/e/2PACX-1vQ702KYrLAJsR_peGN2CjJZ28FNqeZyNXN_7nLv6pMpEPMRDxLKqqkXKqbGm8NvWIU0NOCoy7q_jRgN/pub?gid=0&single=true&output=csv"; 
const BACKEND_URL = "https://coffeemoll.onrender.com"; 

// Cache Busting для таблицы
const cacheBuster = new Date().getTime();
const GOOGLE_SHEET_URL = "https://corsproxy.io/?" + encodeURIComponent(RAW_SHEET_URL + "&t=" + cacheBuster);

const CATEGORY_ORDER = ["ИЗБРАННОЕ", "Популярное", "Сезонное", "Кофе", "Чай", "Холодные напитки", "Молочные коктейли", "Завтраки", "Круассаны", "Хот-доги", "Салаты", "Пицца", "Пасты", "Закуски"];

let PRICES = { alt_milk: 400, add_milk: 50, add_cream: 50, add_honey: 100, add_lemon: 50, syrup: 0, shot: 300, hd_cheese: 150, hd_jalapeno: 150, hd_onion: 150, egg: 70, sauce: 150, cinnamon: 0 };
let SYRUPS = ["Карамель", "Ваниль", "Орех"];
let SAUCES = ["Кетчуп"];
let ALT_MILKS = ["Кокосовое"];
let PROMOS_ENABLED = true; 
let OPEN_HOUR = 9;
let CLOSE_HOUR = 23;

const MOD_LABELS = { milk: "Доп. молоко", cream: "Доп. сливки", honey: "Мед", lemon: "Лимон", syrup: "Сироп", shot: "Доп. шот", egg: "Доп. яйцо", cheese: "Сыр", jalapeno: "Халапеньо", onion: "Жареный лук", sauce: "Соус", alt_milk: "Альт. молоко", cinnamon: "Корица" };
const UPSELL_RULES = { "КОФЕ": ["КРУАССАНЫ", "ЗАКУСКИ"], "ЧАЙ": ["КРУАССАНЫ", "ЗАКУСКИ"], "ПИЦЦА": ["ХОЛОДНЫЕ НАПИТКИ", "ЛИМОНАДЫ"], "ПАСТЫ": ["ХОЛОДНЫЕ НАПИТКИ", "ЛИМОНАДЫ"], "ХОТ-ДОГИ": ["ХОЛОДНЫЕ НАПИТКИ", "ЛИМОНАДЫ"], "ЗАВТРАКИ": ["КОФЕ", "ЧАЙ"] };

let menuData = [];
let cart = [];
let currentModalProduct = null;
let upsellShown = false;
let currentUpsellItem = null;
let appliedDiscount = 0; 
let appliedPromoCode = ""; 
let favorites = JSON.parse(localStorage.getItem('coffee_favorites')) || [];
let loadingInterval;

// --- ИНИЦИАЛИЗАЦИЯ ПОСЛЕ ЗАГРУЗКИ DOM ---
document.addEventListener('DOMContentLoaded', () => {
    try {
        startLoadingAnimation();
        
        // Привязка маски телефона
        const phoneInputs = [document.getElementById('client-phone'), document.getElementById('payment-phone')];
        phoneInputs.forEach(input => {
            if(input) input.addEventListener('input', applyPhoneMask);
        });

        // Слушатели скролла и ресайза
        window.addEventListener('scroll', handleScroll);
        window.addEventListener('resize', updateHeaderHeight);

        // Запуск загрузки меню
        initMenu();
    } catch (e) {
        console.error("Startup Error:", e);
        showErrorScreen();
    }
});

// --- АНИМАЦИЯ ЗАГРУЗКИ ---
function startLoadingAnimation() {
    const loadingTexts = [
        "Загружаем зерно в кофемолку...",
        "Греем холдер...",
        "Взбиваем молоко...",
        "Настраиваем помол...",
        "Рисуем сердечко на пенке...",
        "Протираем столики...",
        "Достаем свежие круассаны..."
    ];
    let textIdx = 0;
    const mottoEl = document.querySelector('.loading-motto');
    
    if(mottoEl) {
        mottoEl.innerText = loadingTexts[0];
        loadingInterval = setInterval(() => {
            mottoEl.style.opacity = 0;
            setTimeout(() => {
                textIdx = (textIdx + 1) % loadingTexts.length;
                mottoEl.innerText = loadingTexts[textIdx];
                mottoEl.style.opacity = 1;
            }, 150);
        }, 2000);
    }
}

function initMenu() {
    checkRepeatOrder();
    updateHeaderHeight();

    // Загружаем ТОЛЬКО меню из Google Sheets
    Papa.parse(GOOGLE_SHEET_URL, {
        download: true, 
        header: true, 
        skipEmptyLines: true,
        complete: function(results) {
            if (loadingInterval) clearInterval(loadingInterval);
            
            if (!results.data || results.data.length === 0) { 
                showErrorScreen(); 
                return; 
            }

            parseSettings(results.data);
            parseMenu(results.data);

            const loader = document.getElementById('loading-screen');
            if(loader) loader.style.display = 'none';
            
            renderMenu();
            
            // Корректировка высоты шапки после рендера
            setTimeout(() => updateHeaderHeight(), 100);
        },
        error: function(err) { 
            console.error("CSV Error:", err); 
            showErrorScreen(); 
        }
    });
}

function parseSettings(data) {
    const settingsRow = data.find(row => { 
        const id = getVal(row, ['id', 'Идентификатор']); 
        return id && id.toUpperCase() === 'SETTINGS'; 
    });
    
    if (settingsRow) {
        const desc = getVal(settingsRow, ['description', 'Описание']);
        const pairs = desc.split(';');
        pairs.forEach(pair => {
            const [key, val] = pair.split('=');
            if(key && val) {
                const k = key.trim(); const v = val.trim();
                if (k === 'alt_milks') ALT_MILKS = v.split(',');
                if (k === 'syrup_names') SYRUPS = v.split(',');
                if (k === 'sauce_names') SAUCES = v.split(',');
                if (k.startsWith('price_') || k.startsWith('hd_') || k === 'br_egg') PRICES[k.replace('price_', '')] = parsePrice(v);
                if (k === 'price_cinnamon') PRICES.cinnamon = parsePrice(v);
                if (k === 'enable_promos') PROMOS_ENABLED = (v.toLowerCase() === 'true');
                if (k === 'open_time') OPEN_HOUR = parseInt(v.split(':')[0]);
                if (k === 'close_time') CLOSE_HOUR = parseInt(v.split(':')[0]);
            }
        });
    }
    
    const promoContainer = document.getElementById('promo-container');
    if (promoContainer) promoContainer.style.display = PROMOS_ENABLED ? 'flex' : 'none';

    if (SYRUPS[0] !== "Нет") SYRUPS.unshift("Нет");
    if (SAUCES[0] !== "Нет") SAUCES.unshift("Нет");
}

function parseMenu(data) {
    let rawData = data.map(item => {
        let pid = getVal(item, ['id', 'Идентификатор']).trim();
        if (!pid || pid.toUpperCase() === 'SETTINGS') return null;
        
        let cat = (getVal(item, ['category', 'Категория']).trim() || "Прочее").toUpperCase();
        if (cat.toLowerCase().includes('лимонад')) cat = 'ХОЛОДНЫЕ НАПИТКИ';
        
        let name = getVal(item, ['name', 'Название']).trim().toUpperCase();
        let priceVal = getVal(item, ['price', 'Цена']);
        let cleanPrice = parsePrice(priceVal);
        let desc = getVal(item, ['description', 'Короткое описание']).trim();
        let fullDesc = getVal(item, ['fullDesc', 'Описание', 'full_description', 'Полное описание']) || desc;
        
        let availVal = getVal(item, ['available', 'В наличии']);
        let isAvailable = (String(availVal).trim().toLowerCase() === 'true' || String(availVal).trim().toLowerCase() === 'да');
        
        let popularVal = getVal(item, ['popular', 'Популярный товар']);
        let isPopular = (String(popularVal).trim().toLowerCase() === 'true' || String(popularVal).trim().toLowerCase() === 'да');
        
        let imgUrl = getVal(item, ['image', 'Фото']).trim();
        let volumeStr = getVal(item, ['volumes', 'Варианты', 'Volumes']);
        let badgeText = getVal(item, ['badge', 'Метка']);
        let modsStr = getVal(item, ['modifiers', 'Модификаторы', 'mods']);

        let mods = {};
        if (modsStr) {
            modsStr.split(/[,;]/).forEach(m => {
                let [k, p] = m.split('=');
                if (k) {
                    k = k.trim();
                    if (k === 'alt_milk' && p && p.includes('|')) { 
                        mods[k] = p.split('|').map(val => parsePrice(val)); 
                    } else {
                        let val = p ? parsePrice(p) : (PRICES[k] || PRICES['add_'+k] || 0);
                        // Defaults if not specified in cell
                        if (!p) {
                            if(k==='alt_milk') val = PRICES.alt_milk;
                            if(k==='shot') val = PRICES.shot;
                            if(k==='syrup') val = PRICES.syrup;
                            if(k==='sauce') val = PRICES.sauce_paid;
                            if(k==='egg') val = PRICES.breakfast_add_egg;
                            if(k==='milk') val = PRICES.add_milk;
                            if(k==='cream') val = PRICES.add_cream;
                            if(k==='honey') val = PRICES.add_honey;
                            if(k==='lemon') val = PRICES.add_lemon;
                            if(k==='cinnamon') val = PRICES.cinnamon;
                        }
                        mods[k] = val;
                    }
                }
            });
        }

        let product = {
            id: pid, cat: cat, name: name, desc: desc, fullDesc: fullDesc, 
            img: imgUrl, available: isAvailable, popular: isPopular,
            price: cleanPrice, basePrice: cleanPrice, badge: badgeText,
            type: 'simple', hasVolumes: false, hasMilk: false, volumes: [], mods: mods
        };

        if (volumeStr && volumeStr.includes('=')) {
            product.type = 'complex'; product.hasVolumes = true;
            product.volumes = volumeStr.split(';').map(v => { 
                let parts = v.split('='); 
                return { n: parts[0].trim(), p: parsePrice(parts[1]) }; 
            });
        }
        if ('alt_milk' in mods) product.hasMilk = true;
        return product;
    }).filter(item => item !== null);

    let popularItems = [];
    rawData.forEach(p => { 
        if (p.popular) { 
            let clone = {...p}; 
            clone.cat = "ПОПУЛЯРНОЕ"; 
            popularItems.push(clone); 
        } 
    });
    menuData = [...popularItems, ...rawData];
}

// --- UI HELPER FUNCTIONS ---

function showErrorScreen() {
    if(loadingInterval) clearInterval(loadingInterval);
    const loader = document.getElementById('loading-screen');
    const error = document.getElementById('error-screen');
    if(loader) loader.style.display = 'none';
    if(error) error.style.display = 'flex';
}

function showToast(msg, type = 'normal') {
    const cont = document.getElementById('toast-container');
    if(!cont) return;
    const toast = document.createElement('div');
    toast.className = `toast ${type}`;
    toast.innerHTML = msg;
    cont.appendChild(toast);
    
    if(type === 'error') tg.HapticFeedback.notificationOccurred('error');
    else tg.HapticFeedback.impactOccurred('light');
    
    setTimeout(() => {
        toast.style.opacity = '0';
        toast.style.transform = 'translate(-50%, 20px)';
        setTimeout(() => toast.remove(), 300);
    }, 3000);
}

// --- SCROLL & HEADER ---
let lastScrollTop = 0;
const delta = 5;

function handleScroll() {
    const st = window.pageYOffset || document.documentElement.scrollTop;
    if(Math.abs(lastScrollTop - st) <= delta) return;
    
    const headerEl = document.querySelector('.header-container');
    if(headerEl) {
        if (st > lastScrollTop && st > 50) {
             headerEl.classList.add('header-hidden');
        } else if (st < lastScrollTop) {
             headerEl.classList.remove('header-hidden');
        }
    }
    lastScrollTop = st <= 0 ? 0 : st;
    updateScrollSpy();
}

function updateHeaderHeight() {
    const headerEl = document.querySelector('.header-container');
    if(headerEl) {
        document.documentElement.style.setProperty('--header-height', headerEl.offsetHeight + 'px');
    }
}

function updateScrollSpy() {
    const sections = document.querySelectorAll('.category-section');
    const navBtns = document.querySelectorAll('.nav-btn');
    let current = '';
    
    if (window.scrollY < 100 && sections.length > 0) {
         current = sections[0].getAttribute('id');
    } else {
        sections.forEach(section => {
            const sectionTop = section.offsetTop;
            if (window.scrollY >= sectionTop - 150) {
                current = section.getAttribute('id');
            }
        });
    }
    
    navBtns.forEach(btn => {
        btn.classList.remove('active');
        if (current && btn.getAttribute('href') === '#' + current) {
            btn.classList.add('active');
            btn.scrollIntoView({ behavior: 'smooth', inline: 'center', block: 'nearest' });
        }
    });
}

// --- PROMO & CART LOGIC ---

async function applyPromo() {
    const codeInput = document.getElementById('promo-code');
    const btn = document.getElementById('apply-promo-btn');
    const code = codeInput.value.trim().toUpperCase();
    
    if (!code) return;

    btn.disabled = true;
    btn.innerText = "...";
    codeInput.style.borderColor = "#eee";

    try {
        const response = await fetch(`${BACKEND_URL}/api/check_promo`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ 
                code: code,
                userId: tg.initDataUnsafe?.user?.id || 0
            })
        });

        if (!response.ok) throw new Error('Network response was not ok');
        const data = await response.json();

        if (data.status === "OK") {
            appliedDiscount = data.discount;
            appliedPromoCode = code;
            codeInput.style.borderColor = "#88d100";
            showToast(`Промокод применен! -${Math.round(appliedDiscount * 100)}%`);
        } else if (data.status === "USED") {
            appliedDiscount = 0; appliedPromoCode = "";
            codeInput.style.borderColor = "red";
            showToast("Вы уже использовали этот код", 'error');
        } else if (data.status === "LIMIT") {
            appliedDiscount = 0; appliedPromoCode = "";
            codeInput.style.borderColor = "red";
            showToast("Лимит промокода исчерпан", 'error');
        } else {
            appliedDiscount = 0; appliedPromoCode = "";
            codeInput.style.borderColor = "red";
            showToast("Неверный промокод", 'error');
        }
        calculateCheckoutTotal();

    } catch (error) {
        console.error('Promo check failed:', error);
        showToast("Ошибка проверки промокода", 'error');
    } finally {
        btn.disabled = false;
        btn.innerText = "OK";
    }
}

// --- RENDER FUNCTIONS ---

function getDisplayData() {
    const favIds = new Set(favorites);
    const favItems = [];
    const normalItems = [];
    const addedToFav = new Set();

    menuData.forEach(p => {
        if (favIds.has(String(p.id)) && !addedToFav.has(p.id)) {
            favItems.push({...p, cat: "ИЗБРАННОЕ"});
            addedToFav.add(p.id);
        }
        normalItems.push(p);
    });
    
    return [...favItems, ...normalItems];
}

function renderNav() {
    const navContainer = document.getElementById('nav-container');
    const displayData = getDisplayData();
    const uniqueCats = [...new Set(displayData.map(p => p.cat))];
    const CATEGORY_ORDER_UPPER = CATEGORY_ORDER.map(c => c.toUpperCase());
    const sortedCats = uniqueCats.sort((a, b) => {
        const idxA = CATEGORY_ORDER_UPPER.indexOf(a); const idxB = CATEGORY_ORDER_UPPER.indexOf(b);
        return (idxA === -1 ? 999 : idxA) - (idxB === -1 ? 999 : idxB);
    });
    
    navContainer.innerHTML = '';
    sortedCats.forEach((cat) => {
        const btn = document.createElement('a'); btn.className = 'nav-btn'; 
        btn.innerText = cat; btn.href = `#cat-${cat.replace(/\s+/g, '-')}`;
        btn.onclick = (e) => { e.preventDefault(); const section = document.getElementById(`cat-${cat.replace(/\s+/g, '-')}`); if(section) { const y = section.getBoundingClientRect().top + window.scrollY - 140; window.scrollTo({top: y, behavior: 'smooth'}); } };
        navContainer.appendChild(btn);
    });
}

function renderMenu() {
    const app = document.getElementById('app'); if (!app) return;
    renderNav(); checkWorkingHours();
    
    const displayData = getDisplayData();
    const uniqueCats = [...new Set(displayData.map(p => p.cat))];
    const CATEGORY_ORDER_UPPER = CATEGORY_ORDER.map(c => c.toUpperCase());
    const sortedCats = uniqueCats.sort((a, b) => {
        const idxA = CATEGORY_ORDER_UPPER.indexOf(a); const idxB = CATEGORY_ORDER_UPPER.indexOf(b);
        return (idxA === -1 ? 999 : idxA) - (idxB === -1 ? 999 : idxB);
    });
    app.innerHTML = '';

    sortedCats.forEach(cat => {
        const section = document.createElement('div'); section.className = 'category-section'; section.id = `cat-${cat.replace(/\s+/g, '-')}`; 
        section.innerHTML = `<div class="category-separator">${cat}</div>`;
        const grid = document.createElement('div'); grid.className = 'grid';
        
        displayData.filter(p => p.cat === cat).forEach((p, index) => {
            const card = document.createElement('div'); card.className = 'card'; card.style.animationDelay = `${index * 0.05}s`;
            let isOutOfStock = !p.available; let isDisabled = isOutOfStock; let btnText = "ВЫБРАТЬ";
            if (isOutOfStock) { btnText = "НЕТ В НАЛИЧИИ"; card.classList.add('unavailable'); }
            
            let displayPrice = 0;
            if (p.hasVolumes && p.volumes.length > 0) displayPrice = `от ${p.volumes[0].p}`;
            else displayPrice = p.price || p.basePrice;
            
            let itemImage = p.img;
            if (!itemImage) {
                if (cat === "ИЗБРАННОЕ") itemImage = "https://cdn-icons-png.flaticon.com/512/210/210545.png"; 
                else itemImage = getImageByCategory(p.cat);
            }
            
            const badgeHtml = isOutOfStock ? `<div class="unavailable-badge">Закончилось</div>` : '';
            let cardBadgeHtml = '';
            if (p.badge && !isOutOfStock) {
                let bClass = 'card-badge'; const txt = p.badge.toLowerCase();
                if(txt.includes('хит') || txt.includes('hot')) bClass += ' badge-hot';
                else if(txt.includes('new') || txt.includes('новинка')) bClass += ' badge-new';
                else bClass += ' badge-green';
                cardBadgeHtml = `<div class="${bClass}">${p.badge}</div>`;
            }

            const isFav = favorites.includes(String(p.id));
            const favHtml = `<div class="fav-btn ${isFav ? 'active' : ''}" data-id="${p.id}" onclick="toggleFavorite(event, '${p.id}')">${isFav ? '♥' : '♡'}</div>`;
            
            card.onclick = () => handleItemClick(p.id);
            
            card.innerHTML = `
            <div class="card-img-container card-skeleton">
                <img src="${itemImage}" alt="${p.name}" loading="lazy" decoding="async" onload="this.classList.add('loaded'); this.parentElement.classList.remove('card-skeleton')">
                ${badgeHtml}${cardBadgeHtml}${favHtml}
            </div>
            <h3>${p.name}</h3>
            ${p.desc ? `<div class="desc">${p.desc}</div>` : ''}
            <div class="price-row"><div class="price">${displayPrice} ₸</div><button class="add-btn" ${isDisabled ? 'disabled' : ''}>${btnText}</button></div>`;
            grid.appendChild(card);
        });
        section.appendChild(grid); app.appendChild(section);
    });
}

function handleItemClick(id) {
    const product = menuData.find(p => String(p.id) === String(id));
    if (!product || !product.available) return;
    openComplexModal(product);
}

function toggleFavorite(e, id) {
    e.stopPropagation(); 
    id = String(id);
    const idx = favorites.indexOf(id);
    if(idx > -1) favorites.splice(idx, 1);
    else favorites.push(id);
    localStorage.setItem('coffee_favorites', JSON.stringify(favorites));
    renderMenu();
    tg.HapticFeedback.impactOccurred('medium');
}

function getImageByCategory(cat) { const map = { "Кофе": "https://cdn-icons-png.flaticon.com/512/751/751621.png", "Завтраки": "https://cdn-icons-png.flaticon.com/512/2771/2771406.png", "Круассаны": "https://cdn-icons-png.flaticon.com/512/3014/3014528.png", "Салаты": "https://cdn-icons-png.flaticon.com/512/2062/2062296.png", "Чай": "https://cdn-icons-png.flaticon.com/512/3054/3054889.png", "Лимонады": "https://cdn-icons-png.flaticon.com/512/2447/2447137.png", "Коктейли": "https://cdn-icons-png.flaticon.com/512/3081/3081986.png", "Хот-доги": "https://cdn-icons-png.flaticon.com/512/1980/1980788.png", "Пицца": "https://cdn-icons-png.flaticon.com/512/3132/3132693.png", "Закуски": "https://cdn-icons-png.flaticon.com/512/5787/5787016.png", "Сезонное": "https://cdn-icons-png.flaticon.com/512/4421/4421089.png", "Смузи": "https://cdn-icons-png.flaticon.com/512/1147/1147588.png", "Холодные напитки": "https://cdn-icons-png.flaticon.com/512/956/956850.png", "Пасты": "https://cdn-icons-png.flaticon.com/512/3081/3081840.png" }; return map[cat] || "https://cdn-icons-png.flaticon.com/512/751/751621.png"; }

// --- UTILS ---
function parsePrice(val) {
    if (!val) return 0;
    const clean = String(val).replace(/\D/g, '');
    return clean ? parseInt(clean, 10) : 0;
}

function getVal(item, keys) {
    for (let k of keys) {
        if (item[k] !== undefined) return item[k];
        const foundKey = Object.keys(item).find(objKey => objKey.trim().toLowerCase() === k.toLowerCase());
        if (foundKey) return item[foundKey];
    }
    return "";
}

function applyPhoneMask(event) {
    const input = event.target;
    let value = input.value.replace(/\D/g, '');
    if (!value) { input.value = ''; return; }
    if (['7', '8'].includes(value[0])) value = value.substring(1);
    if (value.length > 10) value = value.substring(0, 10);
    let formattedValue = '+7 ';
    if (value.length > 0) formattedValue += '(' + value.substring(0, 3);
    if (value.length >= 4) formattedValue += ') ' + value.substring(3, 6);
    if (value.length >= 7) formattedValue += '-' + value.substring(6, 8);
    if (value.length >= 9) formattedValue += '-' + value.substring(8, 10);
    input.value = formattedValue;
}

// --- CART & ORDER LOGIC ---
function checkUpsell(triggerProduct) {
    if (upsellShown) return;
    const triggerCat = triggerProduct.cat.toUpperCase();
    let targetCats = [];
    for (const [key, targets] of Object.entries(UPSELL_RULES)) {
        if (triggerCat.includes(key)) { targetCats = targets; break; }
    }
    if (targetCats.length === 0) return;
    const potential = menuData.filter(p => p.available && targetCats.some(tc => p.cat.toUpperCase().includes(tc)) && p.id !== triggerProduct.id);
    if (potential.length === 0) return;

    currentUpsellItem = potential[Math.floor(Math.random() * potential.length)];
    document.querySelector('.upsell-title').innerText = "К ЭТОМУ ОТЛИЧНО ПОДОЙДЕТ!";
    document.querySelector('.upsell-img').src = currentUpsellItem.img || getImageByCategory(currentUpsellItem.cat);
    document.querySelector('.upsell-product').innerText = currentUpsellItem.name;
    let price = currentUpsellItem.price || currentUpsellItem.basePrice;
    if (currentUpsellItem.hasVolumes && currentUpsellItem.volumes.length > 0) price = currentUpsellItem.volumes[0].p;
    document.getElementById('upsell-price').innerText = price + " ₸";
    setTimeout(() => { document.getElementById('upsell-modal').style.display = 'flex'; upsellShown = true; }, 500);
}

function closeUpsell() { document.getElementById('upsell-modal').style.display = 'none'; }

function acceptUpsell() {
    if (!currentUpsellItem) return;
    closeUpsell();
    if (currentUpsellItem.type === 'complex' || currentUpsellItem.hasMilk || Object.keys(currentUpsellItem.mods).length > 0) {
        setTimeout(() => { openComplexModal(currentUpsellItem); }, 100);
    } else {
            let price = currentUpsellItem.price || currentUpsellItem.basePrice;
            addToCart({ name: currentUpsellItem.name, price: price, options: [], cat: currentUpsellItem.cat });
    }
}

function toggleAltMilk() {
    const altRadio = document.querySelector('input[name="milk"][value="alt"]');
    const selector = document.getElementById('alt-milk-selector');
    if (!selector) return;
    selector.style.display = (altRadio && altRadio.checked) ? 'block' : 'none';
    recalcPrice();
}

function toggleAccordion() {
    const acc = document.getElementById('extras-accordion');
    acc.classList.toggle('open');
}

function addToCart(item) {
    const existingItem = cart.find(i => i.name === item.name && JSON.stringify(i.options) === JSON.stringify(item.options));
    if (existingItem) {
        existingItem.qty = (existingItem.qty || 1) + 1;
        existingItem.priceTotal = existingItem.price * existingItem.qty;
    } else {
        cart.push({ ...item, qty: 1, priceTotal: item.price });
    }
    updateCartButton();
    tg.HapticFeedback.notificationOccurred('success');
    showToast("Добавлено в корзину");
}

function updateCartButton() { 
    const btn = document.getElementById('cart-btn'); 
    const counter = document.getElementById('cart-count');
    const txt = document.getElementById('cart-text');
    if (cart.length > 0) { 
        const total = cart.reduce((s, i) => s + (i.priceTotal || i.price), 0); 
        const count = cart.reduce((c, i) => c + (i.qty || 1), 0);
        btn.style.display = 'block'; 
        txt.innerText = `Оформить: ${total} ₸`;
        counter.innerText = count;
        counter.style.animation = 'none';
        counter.offsetHeight; 
        counter.style.animation = 'pop 0.3s';
    } else { btn.style.display = 'none'; } 
}

function renderCartItems() { 
    const c = document.getElementById('cart-list-container'); 
    c.innerHTML = ''; 
    cart.forEach((item, i) => { 
        const vOpts = item.options ? item.options.filter(o=>o&&o!=="Без сахара").join(', ') : ''; 
        const priceToShow = item.priceTotal || item.price;
        const qty = item.qty || 1;
        c.innerHTML += `<div class="cart-item">
            <div class="cart-item-info"><div class="cart-item-title">${item.name.toUpperCase()}</div><div class="cart-item-opts">${vOpts}</div><div class="cart-item-price-total">${priceToShow} ₸</div></div>
            <div class="cart-qty-controls"><div class="cart-qty-btn" onclick="decQty(${i})">−</div><div class="cart-qty-val">${qty}</div><div class="cart-qty-btn" onclick="incQty(${i})">+</div></div>
        </div>`; 
    }); 
}

function incQty(i) {
    cart[i].qty++;
    cart[i].priceTotal = cart[i].price * cart[i].qty;
    renderCartItems(); updateCartButton(); calculateCheckoutTotal();
}
function decQty(i) {
    if (cart[i].qty > 1) {
        cart[i].qty--;
        cart[i].priceTotal = cart[i].price * cart[i].qty;
    } else { cart.splice(i, 1); }
    renderCartItems(); updateCartButton(); calculateCheckoutTotal();
    if (cart.length === 0) closeCheckout();
}

function openCheckout() { 
    if (cart.length === 0) return; 
    document.getElementById('checkout-modal').style.display = 'flex'; 
    loadUserData(); 
    if(tg.initDataUnsafe?.user && !document.getElementById('client-name').value) document.getElementById('client-name').value = tg.initDataUnsafe.user.first_name; 
    renderCartItems(); 
    appliedDiscount = 0; 
    calculateCheckoutTotal(); 
    
    const isOpen = checkWorkingHours();
    const hasPizza = cart.some(i => i.cat && i.cat.toLowerCase().includes('пицца'));
    const pizzaTime = isPizzaAvailable();
    document.getElementById('closed-warning').style.display = isOpen ? 'none' : 'block';
    document.getElementById('pizza-warning').style.display = (hasPizza && !pizzaTime) ? 'block' : 'none';
}
function closeCheckout() { document.getElementById('checkout-modal').style.display = 'none'; }
function toggleDelivery() { const isDel = document.getElementById('d-delivery').checked; document.getElementById('address-block').style.display = isDel ? 'block' : 'none'; document.getElementById('delivery-msg').style.display = isDel ? 'block' : 'none'; calculateCheckoutTotal(); }
function togglePayment() { const isCash = document.getElementById('p-cash').checked || document.getElementById('p-card').checked; document.getElementById('payment-phone-block').style.display = isCash ? 'none' : 'block'; }
function toggleTimePicker() { const isTime = document.getElementById('t-time').checked; document.getElementById('time-picker-container').style.display = isTime ? 'block' : 'none'; }

function calculateCheckoutTotal() { 
    let t = cart.reduce((s, i) => s + (i.priceTotal || i.price), 0); 
    if(appliedDiscount > 0) {
        const discountAmount = Math.round(t * appliedDiscount);
        t = t - discountAmount;
        document.getElementById('checkout-total-sum').innerText = `Итого: ${t} ₸ (Скидка -${discountAmount})`;
    } else {
        document.getElementById('checkout-total-sum').innerText = `Итого: ${t} ₸`; 
    }
    return t; 
}

function submitOrder() { 
    if (document.activeElement) { document.activeElement.blur(); }
    const btn = document.getElementById('submit-btn'); 
    btn.disabled = true; btn.innerText = "Отправка..."; 
    const name = document.getElementById('client-name').value.trim(); 
    const phone = document.getElementById('client-phone').value.trim(); 
    const address = document.getElementById('delivery-address').value.trim(); 
    const delTypeEl = document.querySelector('input[name="deliveryType"]:checked');
    const delType = delTypeEl ? delTypeEl.value : 'pickup';
    let prettyDelivery = 'Самовывоз'; if(delType==='delivery') prettyDelivery='Доставка'; if(delType==='dinein') prettyDelivery='В зале';
    const payTypeEl = document.querySelector('input[name="paymentType"]:checked');
    const payType = payTypeEl ? payTypeEl.value : 'Наличными';
    const payPhone = document.getElementById('payment-phone').value.trim(); 
    let comment = document.getElementById('order-comment').value.trim(); 
    
    const timeTypeEl = document.querySelector('input[name="orderTimeType"]:checked');
    if(timeTypeEl && timeTypeEl.value === 'time') {
        const specTime = document.getElementById('specific-time').value;
        if(!specTime) { showToast("Выберите время", 'error'); btn.disabled = false; btn.innerText = "Подтвердить заказ"; return; }
        comment = `⏰ К ${specTime}. ${comment}`;
    } else { comment = `⚡ Как можно скорее. ${comment}`; }

    if (!name || !phone) { showToast("Заполните имя и телефон", 'error'); btn.disabled = false; btn.innerText = "Подтвердить заказ"; return; } 
    if (delType === 'delivery' && !address) { showToast("Укажите адрес", 'error'); btn.disabled = false; btn.innerText = "Подтвердить заказ"; return; } 
    if ((payType === 'Kaspi' || payType === 'Halyk') && !payPhone) { showToast("Укажите номер счета", 'error'); btn.disabled = false; btn.innerText = "Подтвердить заказ"; return; } 
    
    saveUserData(name, phone, address); 
    const total = calculateCheckoutTotal(); 
    const orderData = { 
        cart: cart, total: total, 
        info: { name, phone, deliveryType: prettyDelivery, address: (delType==='delivery'?address:''), paymentType: payType, paymentPhone: payPhone, comment, discount: appliedDiscount, promoCode: appliedPromoCode } 
    }; 
    
    localStorage.setItem('coffee_last_order', JSON.stringify(cart));
    tg.sendData(JSON.stringify(orderData)); 
    setTimeout(function() { cart = []; updateCartButton(); closeCheckout(); tg.close(); }, 100);
}

function checkRepeatOrder() {
    const lastOrder = localStorage.getItem('coffee_last_order');
    if (lastOrder) {
        try {
            const items = JSON.parse(lastOrder);
            if (items.length > 0) {
                const uniqueNames = [...new Set(items.map(i => i.name))];
                const desc = uniqueNames.join(', ') + (items.length > uniqueNames.length ? ` (+ еще ${items.length - uniqueNames.length})` : '');
                const price = items.reduce((s, i) => s + (i.priceTotal || i.price), 0);
                document.getElementById('repeat-desc').innerText = desc;
                document.getElementById('repeat-price').innerText = price + " ₸";
                document.getElementById('repeat-order-container').style.display = 'block';
            }
        } catch(e) { console.error(e); }
    }
}

function loadLastOrder() {
    const lastOrder = localStorage.getItem('coffee_last_order');
    if (lastOrder) {
        const items = JSON.parse(lastOrder);
        items.forEach(item => addToCart(item)); 
        document.getElementById('repeat-order-container').style.display = 'none';
    }
}

window.addEventListener('offline', () => { showErrorScreen(); });
window.addEventListener('online', () => { location.reload(); });
