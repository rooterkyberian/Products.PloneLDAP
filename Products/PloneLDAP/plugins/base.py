import logging
import sys
from Acquisition import aq_base
from AccessControl import ClassSecurityInfo
from Products.PluggableAuthService.utils import classImplements, createViewName
from Products.PluggableAuthService.PluggableAuthService import \
        _SWALLOWABLE_PLUGIN_EXCEPTIONS

from Products.PluggableAuthService.interfaces.plugins import \
     IAuthenticationPlugin, IRolesPlugin, \
     ICredentialsResetPlugin, IPropertiesPlugin, IGroupEnumerationPlugin

from Products.PlonePAS.interfaces.group import IGroupIntrospection, \
                                               IGroupManagement
from Products.PlonePAS.interfaces.capabilities import IGroupCapability
from Products.PlonePAS.interfaces.plugins import IMutablePropertiesPlugin
from Products.PlonePAS.plugins.group import PloneGroup
from Products.PloneLDAP.plugins.property import LDAPPropertySheet

logger = logging.getLogger("PloneLDAP")


class PloneLDAPPluginBaseMixin:
    security = ClassSecurityInfo()

    security.declarePrivate('getPropertiesForUser')
    def getPropertiesForUser(self, user, request=None):
        """ Fullfill PropertiesPlugin requirements """
        return LDAPPropertySheet(self.id, user)


    security.declarePrivate('setPropertiesForUser')
    def setPropertiesForUser(self, user, propertysheet):
        """Set the properties of a user or group based on the contents of a
        property sheet. Needed for IMutablePropertiesPlugin.

       This is probably unused code: PlonePAS uses IMutablePropertySheet
       instead.
        """
        acl = self._getLDAPUserFolder()

        if acl is None:
            return

        unmangled_userid = self._demangle(user.getId())
        if unmangled_userid is None:
            return

        ldap_user = acl.getUserById(unmangled_userid)

        if ldap_user is None:
            return

        schemaproperties = dict([(x['public_name'], x['ldap_name']) \
                for x in acl.getSchemaConfig().values() if x['public_name']])
        multivaluedprops = [x['public_name'] for x in acl.getSchemaConfig().values() \
               if x['multivalued']]

        changes={}
        for (key,value) in propertysheet.propertyItems():
            if key in schemaproperties and key!=acl._rdnattr:
                if key in multivaluedprops:
                    changes[key] = [x.strip() for x in value.split(';')]
                else:
                    changes[key] = [value.strip()]
                    changes[key] = [value.strip()]

        acl._delegate.modify(ldap_user.dn, changes)

    #############
    # PlonePAS specific IGroupIntrospection methods
    #############

    ### IGroupIntrospection methods
    
    security.declarePrivate('getGroupById')
    def getGroupById(self, group_id, default=None):
        plugins = self._getPAS()._getOb('plugins')

        group_id = self._verifyGroup(plugins, group_id=group_id)
        title = None

        if not group_id:
            return default

        return self._findGroup(plugins, group_id, title)

    security.declarePrivate('getGroups')
    def getGroups(self):
        groups = [self.getGroupById(x['id']) for x in self.enumerateGroups()]
        return groups


    security.declarePrivate('getGroupIds')
    def getGroupIds(self):
        groupIds = [x['id'] for x in self.enumerateGroups()]
        return tuple(groupIds)

    security.declarePrivate('getGroupMembers')
    def getGroupMembers(self, group_id):
        groups = ((group_id, None),)
        members = self.acl_users.getGroupedUsers(groups)
        
        usernames = [x.getUserName() for x in members]
        
        return usernames


    ### IGroupCapability methods

    def allowGroupAdd(self, principal_id, group_id):
        plugins = self._getPAS()._getOb('plugins')

        group_id = self._verifyGroup(plugins, group_id=group_id)
        user = self.acl_users.getUserById(principal_id)

        if group_id and user:
            return True
        
        return False

    def allowGroupRemove(self, principal_id, group_id):
        return self.allowGroupAdd(principal_id, group_id)


    ### IGroupManagement methods

    security.declarePrivate('addGroup')
    def addGroup(self, id, **kw):
        self.acl_users.manage_addGroup(id)


    security.declarePrivate('addPrincipalToGroup')
    def addPrincipalToGroup(self, principal_id, group_id):
        plugins = self._getPAS()._getOb('plugins')

        group_id = self._verifyGroup(plugins, group_id=group_id)
        user = self.acl_users.getUserById(principal_id)

        if group_id and user:
            userDN = user.getUserDN()
            current_groups = self.acl_users.getGroups(dn=userDN, attr='dn')
            all_groups = self.acl_users.getGroups()
            newGroupDN = None
            x = 0
            while not newGroupDN and x < len(all_groups):
                group_cn, group_dn = all_groups[x]
                if group_cn == group_id:
                    newGroupDN = group_dn
                x += 1
            if newGroupDN:
                current_groups.append(newGroupDN)

            self.acl_users.manage_editUserRoles(userDN, current_groups)
            return True
        else:
            return False

    security.declarePrivate('updateGroup')
    def updateGroup(self, id, **kw):
        raise NotImplementedError()


    security.declarePrivate('setRolesForGroup')
    def setRolesForGroup(self, group_id, roles=()):
        raise NotImplementedError()


    security.declarePrivate('removeGroup')
    def removeGroup(self, group_id):
        plugins = self._getPAS()._getOb('plugins')

        group_id = self._verifyGroup(plugins, group_id=group_id)

        if group_id:
            all_groups = self.acl_users.getGroups(attr='dn')
            groupDN = None
            x = 0
            while groupDN is None and x < len(all_groups):
                group_cn, group_dn = all_groups[x]
                if group_cn == group_id:
                    groupDN = group_dn
                
                x += 1
                
            if groupDN:
                self.acl_users.manage_deleteGroups((groupDN,))


    security.declarePrivate('removePrincipalFromGroup')
    def removePrincipalFromGroup(self, principal_id, group_id):
        plugins = self._getPAS()._getOb('plugins')

        group_id = self._verifyGroup(plugins, group_id=group_id)
        user = self.acl_users.getUserById(principal_id)

        if group_id and user:
            userDN = user.getUserDN()
            current_groups = self.acl_users.getGroups(dn=userDN, attr='dn')
            all_groups = dict(self.acl_users.getGroups())
            group_dn = all_groups[group_id]

            new_groups = [ g for g in current_groups if g!=group_dn ]

            if len(new_groups) != len(current_groups):
                self.acl_users.manage_editUserRoles(userDN, new_groups)

            return True
        
        return False
    
    # The following _ methods gracefuly adapted from PlonePAS.group.GroupManager
    security.declarePrivate('_createGroup')
    def _createGroup(self, plugins, group_id, name):
        """ Create group object. For users, this can be done with a
        plugin, but I don't care to define one for that now. Just uses
        PloneGroup.  But, the code's still here, just commented out.
        This method based on PluggableAuthervice._createUser
        """

        #factories = plugins.listPlugins(IUserFactoryPlugin)

        #for factory_id, factory in factories:

        #    user = factory.createUser(user_id, name)

        #    if user is not None:
        #        return user.__of__(self)

        return PloneGroup(group_id, name).__of__(self)
        

    security.declarePrivate('_findGroup')
    def _findGroup(self, plugins, group_id, title=None, request=None):
        """ group_id -> decorated_group
        This method based on PluggableAuthService._findGroup
        """

        # See if the group can be retrieved from the cache
        view_name = '_findGroup-%s' % group_id
        keywords = { 'group_id' : group_id
                   , 'title' : title
                   }
        group = self.ZCacheable_get(view_name=view_name
                                  , keywords=keywords
                                  , default=None
                                 )

        if group is None:

            group = self._createGroup(plugins, group_id, title)

            propfinders = plugins.listPlugins(IPropertiesPlugin)
            for propfinder_id, propfinder in propfinders:

                data = propfinder.getPropertiesForUser(group, request)
                if data:
                    group.addPropertysheet(propfinder_id, data)

            groups = self._getPAS()._getGroupsForPrincipal(group, request
                                                , plugins=plugins)
            group._addGroups(groups)

            rolemakers = plugins.listPlugins(IRolesPlugin)

            for rolemaker_id, rolemaker in rolemakers:

                roles = rolemaker.getRolesForPrincipal(group, request)

                if roles:
                    group._addRoles(roles)

            group._addRoles(['Authenticated'])

            # Cache the group if caching is enabled
            base_group = aq_base(group)
            if getattr(base_group, '_p_jar', None) is None:
                self.ZCacheable_set(base_group
                                   , view_name=view_name
                                   , keywords=keywords
                                  )

        return group

    security.declarePrivate('_verifyGroup')
    def _verifyGroup(self, plugins, group_id=None, title=None):

        """ group_id -> boolean
        This method based on PluggableAuthService._verifyUser
        """
        criteria = {}

        if group_id is not None:
            criteria[ 'id' ] = group_id
            criteria[ 'exact_match' ] = True

        if title is not None:
            criteria[ 'title' ] = title

        if criteria:
            view_name = createViewName('_verifyGroup', group_id)
            cached_info = self.ZCacheable_get(view_name=view_name
                                             , keywords=criteria
                                             , default=None
                                            )

            if cached_info is not None:
                return cached_info


            enumerators = plugins.listPlugins(IGroupEnumerationPlugin)

            for enumerator_id, enumerator in enumerators:
                try:
                    info = enumerator.enumerateGroups(**criteria)

                    if info:
                        id = info[0]['id']
                        # Put the computed value into the cache
                        self.ZCacheable_set(id
                                           , view_name=view_name
                                           , keywords=criteria
                                           )
                        return id

                except _SWALLOWABLE_PLUGIN_EXCEPTIONS:
                    logger.info('PluggableAuthService: GroupEnumerationPlugin %s error' %
                            enumerator_id, error=sys.exc_info())

        return 0
    
    
classImplements( PloneLDAPPluginBaseMixin
               , IAuthenticationPlugin
               , ICredentialsResetPlugin
               , IPropertiesPlugin
               , IMutablePropertiesPlugin
               , IRolesPlugin
               , IGroupIntrospection
               , IGroupManagement
               , IGroupCapability
               )