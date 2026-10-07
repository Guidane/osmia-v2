from django.contrib import admin

from .models import Attribute, Category, Part, PartAttributeValue


class AttributeInline(admin.TabularInline):
    model = Attribute
    extra = 1


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ('__str__', 'parent')
    search_fields = ('name',)
    inlines = [AttributeInline]


class PartAttributeValueInline(admin.TabularInline):
    model = PartAttributeValue
    extra = 0


@admin.register(Part)
class PartAdmin(admin.ModelAdmin):
    list_display = ('part_number', 'name', 'category', 'unit', 'is_active')
    list_filter = ('category', 'is_active')
    search_fields = ('part_number', 'name')
    filter_horizontal = ('mates_with', 'fits', 'tools')
    inlines = [PartAttributeValueInline]
