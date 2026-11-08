################################################################################
#
# Project Walnut camera-domain application (local engineering sources)
#
################################################################################

HASSLEFREE_CAMERA_VERSION = 0.1.0-r10
HASSLEFREE_CAMERA_PKGDIR = $(BR2_EXTERNAL_DAOS_CAMERA_PATH)/package/hasslefree
HASSLEFREE_CAMERA_PROJECT_ROOT = $(realpath $(BR2_EXTERNAL_DAOS_CAMERA_PATH)/../../..)
# Never sync the project root: it may contain large disk images and owner data.
HASSLEFREE_CAMERA_SITE = $(HASSLEFREE_CAMERA_PROJECT_ROOT)/camera/native
HASSLEFREE_CAMERA_SITE_METHOD = local
HASSLEFREE_CAMERA_OVERRIDE_SRCDIR_RSYNC_EXCLUSIONS = --exclude=/evidence --exclude=/tests --exclude=/build --exclude=__pycache__
HASSLEFREE_CAMERA_LICENSE = Project-rights-pending, MIT (nlohmann/json), Public Domain (SQLite)
HASSLEFREE_CAMERA_LICENSE_FILES = HF-PROJECT-RIGHTS.json third_party/nlohmann/LICENSE.MIT third_party/sqlite/sqlite3.h
# Private engineering use is not a grant to redistribute project-owned sources.
HASSLEFREE_CAMERA_REDISTRIBUTE = NO
HASSLEFREE_CAMERA_DEPENDENCIES = host-python3 python3 python-pyqt5 qt5quickcontrols2

define HASSLEFREE_CAMERA_COPY_RIGHTS
	$(INSTALL) -m 0644 $(HASSLEFREE_CAMERA_PROJECT_ROOT)/licenses/INVENTORY.json $(@D)/HF-PROJECT-RIGHTS.json
endef
HASSLEFREE_CAMERA_POST_RSYNC_HOOKS += HASSLEFREE_CAMERA_COPY_RIGHTS

define HASSLEFREE_CAMERA_BUILD_CMDS
	$(TARGET_MAKE_ENV) $(MAKE) -C $(@D) -f $(HASSLEFREE_CAMERA_PKGDIR)/native.make \
		CC="$(TARGET_CC)" CXX="$(TARGET_CXX)" \
		CPPFLAGS="$(TARGET_CPPFLAGS)" CFLAGS="$(TARGET_CFLAGS)" \
		CXXFLAGS="$(TARGET_CXXFLAGS)" LDFLAGS="$(TARGET_LDFLAGS)"
endef

define HASSLEFREE_CAMERA_INSTALL_TARGET_CMDS
	$(INSTALL) -D -m 0755 $(@D)/hf-camerad $(TARGET_DIR)/usr/bin/hf-camerad
	$(INSTALL) -D -m 0755 $(@D)/hf-native-worker $(TARGET_DIR)/usr/bin/hf-native-worker
	$(HOST_DIR)/bin/python3 $(HASSLEFREE_CAMERA_PKGDIR)/install_runtime.py \
		--project-root $(HASSLEFREE_CAMERA_PROJECT_ROOT) --target-dir $(TARGET_DIR)
endef

$(eval $(generic-package))
