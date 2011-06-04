#This file is part of Tryton.  The COPYRIGHT file at the top level
#of this repository contains the full copyright notices and license terms.
"Nereid CMS"

from string import Template

from nereid import render_template, current_app, cache, request
from nereid.helpers import slugify, url_for, key_from_list, Pagination
from werkzeug.exceptions import NotFound, InternalServerError

from trytond.pyson import Eval, Not, Equal, Bool, In
from trytond.model import ModelSQL, ModelView, fields
from trytond.transaction import Transaction


class CMSLink(ModelSQL, ModelView):
    """CMS link

    (c) 2010 Tryton Project
    """
    _name = 'nereid.cms.link'
    _description = __doc__

    name = fields.Char('Name', required=True, translate=True)
    model = fields.Selection('models_get', 'Model', required=True)
    priority = fields.Integer('Priority')

    def __init__(self):
        super(CMSLink, self).__init__()
        self._order.insert(0, ('priority', 'ASC'))

    def default_priority(self):
        return 5

    def models_get(self):
        model_obj = self.pool.get('ir.model')
        model_ids = model_obj.search([])
        res = []
        for model in model_obj.browse(model_ids):
            res.append((model.model, model.name))
        return res

CMSLink()


class Menu(ModelSQL, ModelView):
    "Nereid CMS Menu"
    _name = 'nereid.cms.menu'
    _description = __doc__

    name = fields.Char('Name', required=True, 
        on_change=['name', 'unique_identifier'])
    unique_identifier = fields.Char('Unique Identifier', required=True,)
    description = fields.Text('Description')
    website = fields.Many2One('nereid.website', 'WebSite')
    active = fields.Boolean('Active')

    model = fields.Many2One('ir.model', 'Tryton Model', required=True)
    children_field = fields.Many2One('ir.model.field', 'Children',
        domain=[
            ('model', '=', Eval('model')),
            ('ttype', '=', 'one2many')
        ], required=True)
    uri_field = fields.Many2One('ir.model.field', 'URI Field',
        domain=[
            ('model', '=', Eval('model')),
            ('ttype', '=', 'char')
        ], required=True)
    title_field = fields.Many2One('ir.model.field', 'Title Field',
        domain=[
            ('model', '=', Eval('model')),
            ('ttype', '=', 'char')
        ], required=True)
    identifier_field = fields.Many2One('ir.model.field', 'Identifier Field',
        domain=[
            ('model', '=', Eval('model')),
            ('ttype', '=', 'char')
        ], required=True)

    def default_active(self):
        """
        By Default the Menu is active
        """
        return True

    def __init__(self):
        super(Menu, self).__init__()
        self._sql_constraints += [
            ('unique_identifier', 'UNIQUE(unique_identifier, website)',
                'The Unique Identifier of the Menu must be unique.'),
        ]

    def _menu_item_to_dict(self, menu, menu_item):
        """
        :param menu_item: BR of the menu item
        :param menu: BR of the menu set
        """
        if hasattr(menu_item, 'reference') and getattr(menu_item, 'reference'):
            model, id = getattr(menu_item, 'reference').split(',')
            reference = self.pool.get(model).browse(int(id))
            uri = url_for('%s.render' % reference._name, uri=reference.uri)
        else:
            uri = getattr(menu_item, menu.uri_field.name) 
        return {
                'name' : getattr(menu_item, menu.title_field.name),
                'uri' : uri,
            }

    def _generate_menu_tree(self, menu, menu_item):
        """
        :param menu: BrowseRecord of the Menu
        :param menu_item: BrowseRecord of the root menu_item
        """
        result = {'children' : [ ]}
        result.update(self._menu_item_to_dict(menu, menu_item))
        # If children exist iteratively call _generate_..
        children = getattr(menu_item, menu.children_field.name)
        if children:
            for child in children:
                result['children'].append(
                    self._generate_menu_tree(menu, child))
        return result

    def menu_for(self, identifier, ident_field_value, objectified=False):
        """
        Returns a dictionary of menu tree

        :param identifier: The unique identifier from which the menu
                has to be chosen
        :param ident_field_value: The value of the field that has to be 
                looked up on model with search on ident_field
        :param objectified: The value returned is the browse recod of 
                the menu identified rather than a tree.
        """
        # First pick up the menu through identifier
        menu_id = self.search(
            [('unique_identifier', '=', identifier)], limit=1)

        if not menu_id:
            current_app.logger.error(
                "Menu %s could not be identified" % identifier)
            return InternalServerError()

        menu = self.browse(menu_id[0])

        # Get the data from the model
        menu_item_object = self.pool.get(menu.model.model)
        menu_item_id = menu_item_object.search( 
            [(menu.identifier_field.name, '=', ident_field_value)],
            limit=1)
        if not menu_item_id:
            current_app.logger.error(
                "Menu %s could not be identified" % ident_field_value)
            return InternalServerError()

        root_menu_item = menu_item_object.browse(menu_item_id[0])
        if objectified:
            return root_menu_item

        cache_key = key_from_list([
            Transaction().cursor.dbname,
            Transaction().user,
            Transaction().language,
            identifier, ident_field_value,
            'nereid.cms.menu.menu_for',
        ])
        rv = cache.get(cache_key)
        if rv is None:
            rv = self._generate_menu_tree(menu, root_menu_item)
            cache.set(cache_key, rv, 60*60)
        return rv

    def on_change_name(self, vals):
        res = { }
        if vals.get('name') and not vals.get('unique_identifier'):
            res['unique_identifier'] = slugify(vals['name'])
        return res

    def context_processor(self):
        """This function will be called by nereid to update
        the template context. Must return a dictionary that the context
        will be updated with.

        This function is registered with nereid.template.context_processor
        in xml code
        """
        return {'menu_for': self.menu_for}

Menu()


class MenuItem(ModelSQL, ModelView):
    "Nereid CMS Menuitem"
    _name = 'nereid.cms.menuitem'
    _description = __doc__
    _rec_name = 'unique_name'

    title = fields.Char('Title', required=True, 
        on_change=['title', 'unique_name'])
    unique_name = fields.Char('Unique Name', required=True)
    link = fields.Char('Link')
    use_url_builder = fields.Boolean('Use URL Builder'),
    url_for_build = fields.Many2One('nereid.url_rule', 'Rule',
        depends=['use_url_builder'],
        states={
            'required': Equal(Bool(Eval('use_url_builder')), True),
            'invisible': Not(Equal(Bool(Eval('use_url_builder')), True)),
            }),
    values_to_build = fields.Char('Values', depends=['use_url_builder'],
        states={
            'required': Equal(Bool(Eval('use_url_builder')), True),
            'invisible': Not(Equal(Bool(Eval('use_url_builder')), True)),
            }
        )
    full_url = fields.Function(fields.Char('Full URL'), 'get_full_url')
    parent = fields.Many2One('nereid.cms.menuitem', 'Parent Menuitem',)
    child = fields.One2Many('nereid.cms.menuitem', 'parent',
        string='Child Menu Items')
    active = fields.Boolean('Active')
    sequence = fields.Integer('Sequence', required=True)

    reference = fields.Reference('Reference', selection='links_get',)

    def links_get(self):
        cms_link_obj = self.pool.get('nereid.cms.link')
        ids = cms_link_obj.search([])
        request_links = cms_link_obj.browse(ids)
        return [(x.model, x.name) for x in request_links]

    def default_active(self):
        return True

    def default_values_to_build(self):
        return '{ }'

    def __init__(self):
        super(MenuItem, self).__init__()
        self._constraints += [
            ('check_recursion', 'wrong_recursion')
        ]
        self._error_messages.update({
            'wrong_recursion': 
            'Error ! You can not create recursive menuitems.',
        })
        self._order.insert(0, ('sequence', 'ASC'))

    def on_change_title(self,vals):
        res = {}
        if vals.get('title') and not vals.get('unique_name'):
            res['unique_name'] = slugify(vals['title'])
        return res

    def get_rec_name(self, ids, name):
        if not ids:
            return {}
        res = {}
        def _name(menuitem):
            if menuitem.id in res:
                return res[menuitem.id]
            elif menuitem.parent:
                return _name(menuitem.parent) + ' / ' + menuitem.title
            else:
                return menuitem.title
        for menuitem in self.browse(ids):
            res[menuitem.id] = _name(menuitem)
        return res

MenuItem()


class ArticleCategory(ModelSQL, ModelView):
    "Article Categories"
    _name = 'nereid.article.category'
    _description = __doc__
    _rec_name = 'title'

    per_page = 10

    title = fields.Char('Title', size=100,
        required=True, on_change=['title', 'unique_name'], select=1)
    unique_name = fields.Char('Unique Name', required=True, select=1,
        help='Unique Name is used as the uri.')
    active = fields.Boolean('Active', select=2)
    description = fields.Text('Description',)
    template = fields.Many2One('nereid.template', 'Template', required=True)
    articles = fields.One2Many('nereid.cms.article', 'category', 'Articles')

    def default_active(self):
        'Return True' 
        return True

    def __init__(self):
        super(ArticleCategory, self).__init__()
        self._sql_constraints += [
            ('unique_name', 'UNIQUE(unique_name)',
                'The Unique Name of the Category must be unique.'),
        ]

    def on_change_title(self, vals):
        res = { }
        if vals.get('title') and not vals.get('unique_name'):
            res['unique_name'] = slugify(vals['title'])
        return res

    def render(self, uri, page=1):
        """
        Renders the category
        """
        article_obj = self.pool.get('nereid.cms.article')
        # Find in cache or load from DB
        cache_key = 'nereid.article.category.%s.%s' % (
            uri, Transaction().language)
        category_ids = cache.get(cache_key)
        if not category_ids:
            category_ids = self.search([('unique_name', '=', uri)])
            cache.set(cache_key, category_ids, 60*60)
        if not category_ids:
            return NotFound()

        category = self.browse(category_ids[0])
        articles = Pagination(self, [('category', '=', category.id)], 
            page, self.per_page)
        return render_template(
            category.template.name, category=category, articles=articles)

    def get_article_category(self, uri, silent):
        """Returns the browse record of the article category given by uri
        """
        category = self.search([('unique_name', '=', uri)], limit=1)
        if not category and not silent:
            raise RuntimeError("Article category %s not found" % uri)
        return self.browse(category[0]) if category else None

    def context_processor(self):
        """This function will be called by nereid to update
        the template context. Must return a dictionary that the context
        will be updated with.

        This function is registered with nereid.template.context_processor
        in xml code
        """
        return {'get_article_category': self.get_article_category}

ArticleCategory()


class Article(ModelSQL, ModelView):
    "CMS Articles"
    _name = 'nereid.cms.article'
    _description = __doc__
    _rec_name = 'uri'

    uri = fields.Char('URI', required=True, select=1, translate=True)
    title = fields.Char('Title', required=True, select=1, translate=True)
    content = fields.Text('Content', required=True, translate=True)
    template = fields.Many2One('nereid.template', 'Template', required=True)
    active = fields.Boolean('Active')
    category = fields.Many2One('nereid.article.category', 'Category',
        required=True)
    image = fields.Many2One('nereid.static.file', 'Image')
    author = fields.Many2One('company.employee', 'Author')
    create_date = fields.DateTime('Created Date')
    published_on = fields.Date('Published On')
    sequence = fields.Integer('Sequence', required=True)
    reference = fields.Reference('Reference', selection='links_get')

    def links_get(self):
        cms_link_obj = self.pool.get('nereid.cms.link')
        ids = cms_link_obj.search([])
        request_links = cms_link_obj.browse(ids)
        return [(x.model, x.name) for x in request_links]

    def default_active(self):
        return True

    def on_change_title(self, vals):
        res = { }
        if vals.get('title') and not vals.get('uri'):
            res['uri'] = slugify(vals['title'])
        return res

    def default_author(self):
        user_obj = self.pool.get('res.user')

        context = Transaction().context
        if context is None:
            context = {}
        employee_id = None
        if context.get('employee'):
            employee_id = context['employee']
        else:
            user = user_obj.browse(Transaction().user)
            if user.employee:
                employee_id = user.employee.id
        if employee_id:
            return employee_id
        return False

    def default_published_on(self):
        date_obj = self.pool.get('ir.date')
        return date_obj.today()

    def render(self, uri):
        """
        Renders the template
        """
        # Find in cache or load from DB
        cache_key = 'nereid.cms.article.%s.%s' % (uri, Transaction().language)
        article_ids = cache.get(cache_key)
        if not article_ids:
            article_ids = self.search([('uri', '=', uri)])
            cache.set(cache_key, article_ids, 60*60)
        if not article_ids:
            return NotFound()
        article = self.browse(article_ids[0])
        return render_template(article.template.name, article=article)

Article()


class BannerCategory(ModelSQL, ModelView):
    """Collection of related Banners"""
    _name = 'nereid.cms.banner.category'
    _description = __doc__

    name = fields.Char('Name', required=True)
    banners = fields.One2Many('nereid.cms.banner', 'category', 'Banners')
    website = fields.Many2One('nereid.website', 'WebSite')

    def get_article_category(self, uri, silent):
        """Returns the browse record of the article category given by uri
        """
        category = self.search([
            ('unique_name', '=', uri), 
            ('website', '=', request.nereid_website.id)
            ], limit=1)
        if not category and not silent:
            raise RuntimeError("Banner category %s not found" % uri)
        return self.browse(category[0]) if category else None

    def context_processor(self):
        """This function will be called by nereid to update
        the template context. Must return a dictionary that the context
        will be updated with.

        This function is registered with nereid.template.context_processor
        in xml code
        """
        return {'get_banner_category': self.get_banner_category}

BannerCategory()


class Banner(ModelSQL, ModelView):
    """Banner for CMS."""
    _name = 'nereid.cms.banner'
    _description = __doc__

    name = fields.Char('Name', required=True)
    description = fields.Char('Description')
    category = fields.Many2One('nereid.cms.banner.category', 'Category', 
        required=True)

    # Type related data
    type = fields.Selection([
        ('image', 'Image'),
        ('remote_image', 'Remote Image'),
        ('custom_code', 'Custom Code'),
        ], 'Type', required=True)
    file = fields.Many2One('nereid.static.file', 'File',
        states = {
            'required': Equal(Eval('type'), 'image'),
            'invisible': Not(Equal(Eval('type'), 'image'))
            })
    remote_image_url = fields.Char('Remote Image URL',
        states = {
            'required': Equal(Eval('type'), 'remote_image'),
            'invisible': Not(Equal(Eval('type'), 'remote_image'))
            })
    custom_code = fields.Text('Custom Code', translate=True,
        states={
            'required': Equal(Eval('type'), 'custom_code'),
            'invisible': Not(Equal(Eval('type'), 'custom_code'))
            })

    # Presentation related Data
    height = fields.Integer('Height', 
        states = {
            'invisible': Not(In(Eval('type'), ['image', 'remote_image']))
            })
    width = fields.Integer('Width', 
        states = {
            'invisible': Not(In(Eval('type'), ['image', 'remote_image']))
            })
    alternative_text = fields.Char('Alternative Text', 
        states = {
            'invisible': Not(In(Eval('type'), ['image', 'remote_image']))
            })
    click_url = fields.Char('Click URL', translate=True, 
        states = {
            'invisible': Not(In(Eval('type'), ['image', 'remote_image']))
            })

    state = fields.Selection([
        ('published', 'Published'),
        ('archived', 'Archived')
        ], 'State', required=True)
    reference = fields.Reference('Reference', selection='links_get')


    def get_html(self, id):
        """Return the HTML content"""
        banner = self.read(id, ['type', 'click_url', 'file', 
            'remote_image_url', 'custom_code', 'height', 'width', 
            'alternative_text', 'click_url'])
        if banner['type'] == 'image':
            image = Template(
                u'<a href="$click_url">'
                    u'<img src="$file" alt="$alternative_text"'
                    u' width="$width" height="$height"/>'
                u'</a>')
            return image.substitute(**banner)
        elif banner['type'] == 'remote_image':
            image = Template(
                u'<a href="$click_url">'
                    u'<img src="$remote_image_url" alt="$alternative_text"'
                    u' width="$width" height="$height"/>'
                u'</a>')
            return image.substitute(**banner)
        elif banner['type'] == 'custom_code':
            return banner['custom_code']

    def links_get(self):
        cms_link_obj = self.pool.get('nereid.cms.link')
        ids = cms_link_obj.search([])
        request_links = cms_link_obj.browse(ids)
        return [(x.model, x.name) for x in request_links]

Banner()

