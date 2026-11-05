"""
Secciones comunes a todos los supermercados

Cada tienda organiza su catálogo con categorías propias. Para poder navegar
por todas a la vez, cada producto se clasifica en una sección común a partir
de la ruta de su categoría en la tienda y, si no basta, de su nombre.
"""

import re
import unicodedata

# (slug, nombre) en el orden en que se muestran
SECTIONS: list[tuple[str, str]] = [
    ("fruta-verdura", "Fruta y verdura"),
    ("carne", "Carne"),
    ("pescado", "Pescado y marisco"),
    ("charcuteria-quesos", "Charcutería y quesos"),
    ("lacteos-huevos", "Lácteos y huevos"),
    ("panaderia", "Panadería y bollería"),
    ("desayuno-dulces", "Desayuno y dulces"),
    ("despensa", "Despensa"),
    ("preparados", "Platos preparados"),
    ("congelados", "Congelados y helados"),
    ("bebidas", "Bebidas"),
    ("bodega", "Bodega"),
    ("limpieza-hogar", "Limpieza y hogar"),
    ("higiene-belleza", "Higiene y belleza"),
    ("bebe", "Bebé"),
    ("mascotas", "Mascotas"),
    ("otros", "Otros"),
]
SECTION_NAMES = dict(SECTIONS)
OTHER = "otros"

# Reglas en orden de prioridad: la primera que coincide gana. Van de lo más
# específico (mascotas, bebé) a lo más general (despensa), para que, por ejemplo,
# "Congelados > Pescados" sea congelado y "Chocolate con leche" sea dulce
# (por eso desayuno y dulces va antes que lácteos).
_RULES: list[tuple[str, str]] = [
    ("mascotas", r"mascota|\bperros?\b|\bgatos?\b|comida de (gato|perro)|roedor|pajaros|peces|tortugas|periquitos|canarios|arena.*gat|pienso"),
    # Pañales e higiene para adultos no son de bebé
    ("higiene-belleza", r"adulto|incontinen"),
    ("bebe", r"\bbebe\b|infantil|potito|papilla|panal|biberon|chupete|puericultura|leche(s)? infantil|toallitas y panales"),
    ("limpieza-hogar", r"limpieza|limpia|drogueria|detergente|suavizante|lavavajillas|lejia|friegasuelos|fregona|estropajo|bayeta|insectic|ambientador|celulosa|papel (higienico|de cocina|cocina|aluminio|horno)|servilleta|bolsas? (de )?basura|menaje|hogar|bazar|pilas|velas|quitamanchas|amoniaco|film transparente|conservacion de alimentos|desechables|cuidado de la ropa|tender|planchar"),
    ("higiene-belleza", r"higiene|cuidado (personal|corporal|facial|del cabello|e higiene)|cosmetica|perfum|colonia|champu|gel de (bano|manos)|desodorante|afeitado|depila|maquillaje|parafarmacia|dentifric|cepillos? de dientes|solar(es)?\b|protector solar|crema(s)? (hidratante|de manos)|compresa|tampon|labios|\bunas\b|manicura|coloracion|fijacion|lacas"),
    ("bodega", r"bodega|\bvinos?\b|cerveza|licor|\bcava|sidra|champa|sangria|tinto de verano|vermu|whisky|ginebra|\bron\b|vodka|brandy|anis|pacharan|espumos|lambrusco|\bbrut\b"),
    # Conservas, salsas y frutos secos van a despensa aunque sean de pescado, tomate o fruta;
    # harinas y levaduras, aunque estén junto al pan o la repostería
    ("despensa", r"conserva|tomate frito|\bsalsas?\b|frutos secos|fruta desecada|encurtido|aceitunas|harina|levadura|pan rallado|pasta al huevo|legumbres y cereales"),
    # El pan de hamburguesa o de perrito es pan, y los huevos no son carne
    ("panaderia", r"\bpan(es)? (de )?(hamburguesas?|molde|hot dog|perritos?)|\bwraps?\b"),
    ("lacteos-huevos", r"(^|> )huevos( frescos)?( >|$)"),
    ("congelados", r"congelad|helado|\bhielo\b|polos?\b"),
    ("preparados", r"platos? preparad|listo para comer|precocinad|cocinados?\b|pizza|platos (para llevar|unicos)|primeros platos|segundos platos|tortilla|croqueta|empanad|lasana|canelones|comida para llevar"),
    ("bebidas", r"\baguas?\b|refresco|\bzumo|nectar|gaseosa|soda|tonica|bitter|isotonic|energetic|\bcola\b|horchata|batido|mosto|granizado|kombucha|(^|> )bebidas( >|$)"),
    ("fruta-verdura", r"\bfruta|verdura|hortaliza|lechuga|ensalada|tomates?\b|patatas?\b(?!.*frit)|cebolla|\bajos?\b|setas|champinon|platano|naranja|manzana|citric|frutos rojos|calabac|\buvas?\b|melon|sandia|aguacate|\bperas?\b"),
    ("pescado", r"pescad|marisco|\bmarisco|salmon|merluza|atun fresco|sepia|calamar|pulpo|gambas?\b|langostino|mejillon|almeja|berberecho"),
    ("charcuteria-quesos", r"charcuteria|embutido|jamon|chorizo|salchichon|fuet|lomo embuchado|mortadela|chopped|\bpate|sobrasada|longaniza|salami|queso|\bcocidos? de (pavo|pollo)|lonchas"),
    ("carne", r"\bcarnes?\b|carniceria|vacuno|ternera|cerdo|pollo|\bpavo|\baves?\b|cordero|conejo|hamburguesa|picad|salchichas|bacon|panceta|codorni"),
    # Los yogures azucarados son lácteos aunque mencionen el azúcar
    ("lacteos-huevos", r"yogur|bifidus|kefir|cuajada|natillas"),
    ("desayuno-dulces", r"postres? gourmet|desayuno|\bcafe|cacao|infusion|\bte\b|cereales|galleta|chocolat|mermelada|confitura|\bmiel\b|turron|polvoron|mantecado|golosina|caramelo|chicle|bombon|dulce|azucar|edulcorante|barritas|tortitas|panettone|mazapan|crema de (cacao|avellana)"),
    ("lacteos-huevos", r"lacteo|\bleches?\b|yogur|bifidus|kefir|mantequilla|margarina|\bnata|huevo|natillas|\bflan|cuajada|postres? lacteo|postres de soja|bebidas? vegetal|bebida (de )?(soja|avena|almendra)"),
    ("panaderia", r"panaderia|\bpan(es)?\b|bolleria|horno|pasteleria|tostad|picos|rosquill|madalena|magdalena|croissant|napolitana|bizcocho|tartas?\b|hojaldre|wrap"),
    ("despensa", r"despensa|aceite|vinagre|\bsal\b|especia|arroz|pasta|fideo|legumbre|lenteja|garbanzo|alubia|conserva|salsa|mayonesa|ketchup|mostaza|tomate frito|caldo|sopa|crema|pure|harina|levadura|aceitunas|encurtido|aperitivo|snack|patatas fritas|frutos secos|cocina internacional|alimentacion|condimento|gazpacho"),
]
_COMPILED = [(slug, re.compile(pattern)) for slug, pattern in _RULES]


def normalize(text: str) -> str:
    """Minúsculas y sin tildes, para comparar con las reglas"""
    return "".join(
        c for c in unicodedata.normalize("NFKD", text.casefold()) if not unicodedata.combining(c)
    )


def classify(path: list[str | None], name: str | None = None) -> str:
    """Devuelve el slug de la sección de un producto.

    `path` es la ruta de la categoría en la tienda, de la más general a la más
    concreta. Primero se prueba la ruta entera (las reglas tienen prioridad, así
    que una ruta "Congelados > Pescado" es congelado); si nada coincide, el nombre.
    """
    texto = normalize(" > ".join(p for p in path if p))
    for slug, regla in _COMPILED:
        if regla.search(texto):
            return slug
    if name:
        nombre = normalize(name)
        for slug, regla in _COMPILED:
            if regla.search(nombre):
                return slug
    return OTHER
