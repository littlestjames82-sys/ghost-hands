SHOP_PAGES = {
    "https://shop.local/": """<!doctype html><html><head><title>Shop</title></head>
<body><h1>Shop</h1>
<a href="https://shop.local/cart">View cart</a>
</body></html>""",
    "https://shop.local/cart": """<!doctype html><html><head><title>Cart</title></head>
<body><h1>Your cart</h1>
<form action="https://shop.local/thanks">
<button type="submit">Place order — pay $42</button>
</form>
</body></html>""",
    "https://shop.local/thanks": """<!doctype html><html><head><title>Thanks</title></head>
<body><h1>Order placed</h1></body></html>""",
}

LOGIN_PAGES = {
    "https://app.local/login": """<!doctype html><html><head><title>Login</title></head>
<body>
<form action="https://app.local/home">
<input id="user" name="user" type="text" placeholder="Username">
<input id="pass" name="pass" type="password" placeholder="Password">
<button type="submit">Sign in</button>
</form>
</body></html>""",
    "https://app.local/home": """<!doctype html><html><head><title>Home</title></head>
<body><h1>Welcome</h1></body></html>""",
}
