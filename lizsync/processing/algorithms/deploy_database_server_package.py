"""
/***************************************************************************
 *                                                                         *
 *   This program is free software; you can redistribute it and/or modify  *
 *   it under the terms of the GNU General Public License as published by  *
 *   the Free Software Foundation; either version 2 of the License, or     *
 *   (at your option) any later version.                                   *
 *                                                                         *
 ***************************************************************************/
"""

__author__ = '3liz'
__date__ = '2018-12-19'
__copyright__ = '(C) 2018 by 3liz'

import os
import tempfile

from qgis.core import (
    QgsProcessingException,
    QgsProcessingParameterString,
    QgsProcessingParameterFile,
    QgsProcessingParameterBoolean,
    QgsProcessingOutputString,
    QgsProcessingOutputNumber
)
from .tools import (
    lizsyncConfig,
    getUriFromConnectionName,
    get_connection_password_from_ini,
    fetchDataFromSqlQuery,
    run_command,
)
from platform import system as psys
from ...qgis_plugin_tools.tools.i18n import tr
from ...qgis_plugin_tools.tools.algorithm_processing import BaseProcessingAlgorithm


class DeployDatabaseServerPackage(BaseProcessingAlgorithm):
    """
    Exectute SQL on PostgreSQL database
    given host, port, dbname, user and password
    """

    # Constants used to refer to parameters and outputs. They will be
    # used when calling the algorithm from another algorithm, or when
    # calling from the QGIS console.
    CONNECTION_NAME_CENTRAL = 'CONNECTION_NAME_CENTRAL'
    CONNECTION_NAME_CLONE = 'CONNECTION_NAME_CLONE'
    POSTGRESQL_BINARY_PATH = 'POSTGRESQL_BINARY_PATH'
    RECREATE_CLONE_SERVER_ID = 'RECREATE_CLONE_SERVER_ID'
    ZIP_FILE = 'ZIP_FILE'

    OUTPUT_STATUS = 'OUTPUT_STATUS'
    OUTPUT_STRING = 'OUTPUT_STRING'

    def name(self):
        return 'deploy_database_server_package'

    def displayName(self):
        return tr('Deploy a database package to the clone')

    def group(self):
        return tr('02 PostgreSQL synchronization')

    def groupId(self):
        return 'lizsync_postgresql_sync'

    def shortHelpString(self):
        short_help = tr(
            ' Deploy a ZIP archive, previously saved with the'
            ' "Package central database" algorithm, to the chosen clone.'
            ' This ZIP archive, named by default "central_database_package.zip"'
            ' contains data from the central PostgreSQL database.'
        )
        return short_help

    def initAlgorithm(self, config):
        """
        Here we define the inputs and output of the algorithm, along
        with some other properties.
        """
        # LizSync config file from ini
        ls = lizsyncConfig()

        # INPUTS
        # central database
        connection_name_central = ls.variable('postgresql:central/name')
        db_param_a = QgsProcessingParameterString(
            self.CONNECTION_NAME_CENTRAL,
            tr('PostgreSQL connection to the central database'),
            defaultValue=connection_name_central,
            optional=False
        )
        db_param_a.setMetadata({
            'widget_wrapper': {
                'class': 'processing.gui.wrappers_postgis.ConnectionWidgetWrapper'
            }
        })
        self.addParameter(db_param_a)

        # Clone database connection parameters
        connection_name_clone = ls.variable('postgresql:clone/name')
        db_param_b = QgsProcessingParameterString(
            self.CONNECTION_NAME_CLONE,
            tr('PostgreSQL connection to the clone database'),
            defaultValue=connection_name_clone,
            optional=False
        )
        db_param_b.setMetadata({
            'widget_wrapper': {
                'class': 'processing.gui.wrappers_postgis.ConnectionWidgetWrapper'
            }
        })
        self.addParameter(db_param_b)

        # PostgreSQL binary path (with psql, pg_dump, pg_restore)
        postgresql_binary_path = ls.variable('binaries/postgresql')
        self.addParameter(
            QgsProcessingParameterFile(
                self.POSTGRESQL_BINARY_PATH,
                tr('PostgreSQL binary path'),
                defaultValue=postgresql_binary_path,
                behavior=QgsProcessingParameterFile.Folder,
                optional=False
            )
        )

        # Database ZIP archive file
        database_archive_file = ls.variable('general/database_archive_file')
        if not database_archive_file:
            database_archive_file = os.path.join(
                tempfile.gettempdir(),
                'central_database_package.zip'
            )
        # ZIP file
        self.addParameter(
            QgsProcessingParameterFile(
                self.ZIP_FILE,
                tr('Database ZIP archive path'),
                defaultValue=database_archive_file,
                behavior=QgsProcessingParameterFile.File,
                optional=True,
                extension='zip'
            )
        )

        self.addParameter(
            QgsProcessingParameterBoolean(
                self.RECREATE_CLONE_SERVER_ID,
                tr('Recreate clone server id. Do it only to fully reset the clone ID !'),
                defaultValue=False,
                optional=False
            )
        )

        # OUTPUTS
        # Add output for message
        self.addOutput(
            QgsProcessingOutputNumber(
                self.OUTPUT_STATUS, tr('Output status')
            )
        )
        self.addOutput(
            QgsProcessingOutputString(
                self.OUTPUT_STRING, tr('Output message')
            )
        )

    def checkParameterValues(self, parameters, context):

        # Check postgresql binary path
        postgresql_binary_path = parameters[self.POSTGRESQL_BINARY_PATH]
        test_bin = 'psql'
        if psys().lower().startswith('win'):
            test_bin += '.exe'
        has_bin_file = os.path.isfile(
            os.path.join(
                postgresql_binary_path,
                test_bin
            )
        )
        if not has_bin_file:
            return False, tr('The needed PostgreSQL binaries cannot be found in the specified path')

        # Check output zip path
        database_archive_file = self.parameterAsString(parameters, self.ZIP_FILE, context)
        if not os.path.exists(database_archive_file):
            database_archive_file = os.path.join(
                tempfile.gettempdir(),
                'central_database_package.zip'
            )
        ok = os.path.exists(database_archive_file)

        # Check ZIP archive content
        if not ok:
            return False, tr("The ZIP archive does not exists in the specified path") + ": {0}".format(database_archive_file)
        parameters[self.ZIP_FILE] = database_archive_file

        # Check connections
        connection_name_central = parameters[self.CONNECTION_NAME_CENTRAL]
        connection_name_clone = parameters[self.CONNECTION_NAME_CLONE]
        ok, uri, msg = getUriFromConnectionName(connection_name_central, True)
        if not ok:
            return False, msg
        ok, uri, msg = getUriFromConnectionName(connection_name_clone, True)
        if not ok:
            return False, msg

        return super(DeployDatabaseServerPackage, self).checkParameterValues(parameters, context)

    def processAlgorithm(self, parameters, context, feedback):
        """
        Here is where the processing itself takes place.
        """
        output = {
            self.OUTPUT_STATUS: 0,
            self.OUTPUT_STRING: ''
        }
        database_archive_file = self.parameterAsString(parameters, self.ZIP_FILE, context)
        connection_name_central = parameters[self.CONNECTION_NAME_CENTRAL]
        connection_name_clone = parameters[self.CONNECTION_NAME_CLONE]
        postgresql_binary_path = parameters[self.POSTGRESQL_BINARY_PATH]
        recreate_clone_server_id = self.parameterAsBool(
            parameters, self.RECREATE_CLONE_SERVER_ID,
            context
        )

        # store parameters
        ls = lizsyncConfig()
        ls.setVariable('general/database_archive_file', database_archive_file)
        ls.setVariable('postgresql:central/name', connection_name_central)
        ls.setVariable('postgresql:clone/name', connection_name_clone)
        ls.setVariable('binaries/postgresql', postgresql_binary_path)
        ls.save()

        # Check archive
        if not os.path.exists(database_archive_file):
            m = tr('Package not found') + ' : %s' % database_archive_file
            raise QgsProcessingException(m)

        msg = ''
        # Uncompress package
        feedback.pushInfo(tr('UNCOMPRESS PACKAGE') + ' {0}'.format(database_archive_file))
        import zipfile
        dir_path = os.path.dirname(os.path.abspath(database_archive_file))
        try:
            with zipfile.ZipFile(database_archive_file) as t:
                t.extractall(dir_path)
                feedback.pushInfo(tr('Package uncompressed successfully'))
        except Exception:
            m = tr('Package extraction error')
            raise QgsProcessingException(m)

        feedback.pushInfo('')

        # Check needed files
        feedback.pushInfo(tr('CHECK UNCOMPRESSED FILES'))
        sql_file_list = [
            '01_before.sql',
            '02_data.sql',
            '03_after.sql',
            '04_lizsync.sql',
            'sync_id.txt',
            'sync_schemas.txt'
        ]
        for f in sql_file_list:
            if not os.path.exists(os.path.join(dir_path, f)):
                m = tr('One mandatory file has not been found in the ZIP archive') + '  - %s' % f
                raise QgsProcessingException(m)
        feedback.pushInfo(tr('All the mandatory files have been sucessfully found'))

        feedback.pushInfo('')

        # CLONE DATABASE
        # Check if clone database already has a lizsync structure installed
        # Get existing data to avoid recreating server_id for this machine
        feedback.pushInfo(tr('GET EXISTING CLONE DATABASE ID TO AVOID RECREATING SERVER_ID FOR THIS CLONE'))
        clone_id = None
        clone_name = None
        sql = '''
        SELECT table_name
        FROM information_schema.tables
        WHERE table_name = 'server_metadata' and table_schema = 'lizsync';
        '''
        header, data, rowCount, ok, error_message = fetchDataFromSqlQuery(
            connection_name_clone,
            sql
        )
        has_sync = False
        if ok:
            for a in data:
                if a[0] == 'server_metadata':
                    has_sync = True
                    feedback.pushInfo(tr('Clone database already has sync metadata table'))
        else:
            raise QgsProcessingException(error_message)

        # get existing server_id
        if has_sync:
            sql = '''
            SELECT server_id, server_name
            FROM lizsync.server_metadata
            LIMIT 1;
            '''
            header, data, rowCount, ok, error_message = fetchDataFromSqlQuery(
                connection_name_clone,
                sql
            )
            if ok:
                for a in data:
                    clone_id = a[0]
                    clone_name = a[1]
                    feedback.pushInfo(tr('Clone metadata are already set'))
                    feedback.pushInfo(tr('* server id') + ' = {0}'.format(clone_id))
                    feedback.pushInfo(tr('* server name') + ' = {0}'.format(clone_name))
            else:
                raise QgsProcessingException(error_message)

        feedback.pushInfo('')

        # Get last synchro and
        # check if no newer bi-directionnal (partial sync)
        # or archive deployment (full sync)
        # have been made since last deployment
        if has_sync and clone_id:
            feedback.pushInfo(tr('CHECK LAST SYNCHRONIZATION'))
            with open(os.path.join(dir_path, 'sync_id.txt')) as f:
                sync_id = f.readline().strip()
            if not sync_id:
                m = tr('No synchronization ID has been found in the file sync_id.txt')
                raise QgsProcessingException(m)
            sql = '''
                SELECT sync_id
                FROM lizsync.history
                WHERE TRUE
                AND sync_time > (
                    SELECT sync_time
                    FROM lizsync.history
                    WHERE sync_id::text = '{sync_id}'
                )
                AND server_from::text = (
                    SELECT server_id::text
                    FROM lizsync.server_metadata
                    LIMIT 1
                )
                AND '{clone_id}' = ANY (server_to)
            '''.format(
                sync_id=sync_id,
                clone_id=clone_id
            )
            last_sync = None
            header, data, rowCount, ok, error_message = fetchDataFromSqlQuery(
                connection_name_central,
                sql
            )
            if not ok:
                m = error_message + ' ' + sql
                raise QgsProcessingException(m)
            for a in data:
                last_sync = a[0]
            if last_sync:
                m = tr(
                    'Bi-directionnal synchronization has already been made on this clone'
                    ' since the deployment of this package. Abort the current deployment.'
                )
                raise QgsProcessingException(m)
            else:
                feedback.pushInfo(tr(
                    'No previous bi-directionnal synchronization found since the deployment'
                    ' of this package. Everything is ok.'
                ))

        # Get synchronized schemas from text file
        feedback.pushInfo(tr('GET THE LIST OF SYNCHRONIZED SCHEMAS FROM THE FILE sync_schemas.txt'))
        with open(os.path.join(dir_path, 'sync_schemas.txt')) as f:
            sync_schemas = f.readline().strip()
        if sync_schemas == '':
            m = tr('No schema to syncronize')
            raise QgsProcessingException(m)

        feedback.pushInfo(tr('Schema list found in sync_schemas.txt') + ': %s' % sync_schemas)

        feedback.pushInfo('')

        # CLONE DATABASE
        # Run SQL scripts from archive with PSQL command
        feedback.pushInfo(tr('RUN SQL SCRIPT FROM THE DECOMPRESSED ZIP FILE'))
        a_sql = os.path.join(dir_path, '01_before.sql')
        b_sql = os.path.join(dir_path, '02_data.sql')
        c_sql = os.path.join(dir_path, '03_after.sql')
        d_sql = os.path.join(dir_path, '04_lizsync.sql')
        if not os.path.exists(a_sql) or not os.path.exists(b_sql) or not os.path.exists(c_sql):
            m = tr('SQL files not found')
            raise QgsProcessingException(m)
        sql_files = [a_sql, b_sql, c_sql, d_sql]

        # Add additional SQL file if present
        last_sql = os.path.join(dir_path, '99_last.sql')
        if os.path.exists(last_sql):
            sql_files.append(last_sql)

        # Build clone database connection parameters for psql
        status, uri, error_message = getUriFromConnectionName(connection_name_clone)
        if not status or not uri:
            m = tr('Error getting database connection information')
            m += ' ' + error_message
            raise QgsProcessingException(m)

        if uri.service():
            cmdo = [
                'service={0}'.format(uri.service())
            ]
        else:
            cmdo = [
                '-h {0}'.format(uri.host()),
                '-p {0}'.format(uri.port()),
                '-d {0}'.format(uri.database()),
                '-U {0}'.format(uri.username()),
            ]

        # Build psql command to run
        pgbin = 'psql'
        if psys().lower().startswith('win'):
            pgbin += '.exe'
        pgbin = os.path.join(
            postgresql_binary_path,
            pgbin
        )
        if psys().lower().startswith('win'):
            pgbin = '"' + pgbin + '"'

        # Run SQL files
        for i in sql_files:
            try:
                feedback.pushInfo(tr('Loading file') + ' {0} ....'.format(i))
                cmd = [
                          pgbin
                      ] + cmdo + [
                          '--no-password',
                          '-f "{0}"'.format(i)
                      ]
                # feedback.pushInfo('PSQL = %s' % ' '.join(cmd) )
                # Add password if needed
                myenv = {**os.environ}
                if not uri.service():
                    if not uri.password():
                        password = get_connection_password_from_ini(uri)
                        uri.setPassword(password)
                    myenv = {**{'PGPASSWORD': uri.password()}, **os.environ}

                run_command(cmd, myenv, feedback)
                # subprocess.run(
                # " ".join(cmd),
                # shell=True,
                # env=myenv
                # )
                msg += '* {0} -> OK'.format(i.replace(dir_path, ''))

                # Delete SQL scripts
                os.remove(i)

            except Exception:
                m = tr('Error loading file') + ' {0}'.format(i)
                raise QgsProcessingException(m)

            finally:
                feedback.pushInfo('* {0} has been loaded'.format(i.replace(dir_path, '')))
                feedback.pushInfo('')

        feedback.pushInfo('')

        # CLONE DATABASE
        # Add server_id in lizsync.server_metadata if needed
        if not clone_id or recreate_clone_server_id:
            # Generate a new ID
            feedback.pushInfo(tr('ADDING THE SERVER ID IN THE CLONE metadata table'))
            sql = '''
            DELETE FROM lizsync.server_metadata;
            INSERT INTO lizsync.server_metadata (server_name)
            VALUES ( concat('clone',  ' ', md5((now())::text) ) )
            RETURNING server_id, server_name
            '''
        else:
            # Keep the already present ID
            feedback.pushInfo(tr('KEEP THE SERVER ID IN THE CLONE metadata table'))
            sql = '''
            DELETE FROM lizsync.server_metadata;
            INSERT INTO lizsync.server_metadata (server_id, server_name)
            VALUES ( '{0}', '{1}' )
            RETURNING server_id, server_name
            '''.format(
                clone_id,
                clone_name
            )
        header, data, rowCount, ok, error_message = fetchDataFromSqlQuery(
            connection_name_clone,
            sql
        )
        if ok:
            for a in data:
                clone_id = a[0]
                clone_name = a[1]
                feedback.pushInfo(tr('Server metadata in the clone database'))
                feedback.pushInfo(tr('* server id') + ' = {0}'.format(clone_id))
                feedback.pushInfo(tr('* server name') + ' = {0}'.format(clone_name))
        else:
            m = tr('Error while adding server id in clone metadata table')
            m+= ' ' + error_message
            raise QgsProcessingException(m)

        feedback.pushInfo('')

        # CENTRAL DATABASE
        # Add an item in lizsync.synchronized_schemas
        # to know afterward wich schemas to use when performing sync
        feedback.pushInfo(tr('ADDING THE LIST OF SYNCHRONIZED SCHEMAS FOR THIS CLONE IN THE CENTRAL DATABASE '))
        sql = '''
            DELETE FROM lizsync.synchronized_schemas
            WHERE server_id = '{0}';
            INSERT INTO lizsync.synchronized_schemas
            (server_id, sync_schemas)
            VALUES
            ( '{0}', jsonb_build_array( '{1}' ) );

        '''.format(
            clone_id,
            "', '".join([a.strip() for a in sync_schemas.split(',')])
        )
        # feedback.pushInfo(sql)
        header, data, rowCount, ok, error_message = fetchDataFromSqlQuery(
            connection_name_central,
            sql
        )
        if ok:
            msg = tr('List of synchronized schemas added in central database for this clone')
            feedback.pushInfo(msg)
        else:
            m = tr('Error while adding the synchronized schemas in the central database')
            m+= ' ' + error_message
            raise QgsProcessingException(m)

        feedback.pushInfo('')

        # CLONE DATABASE
        # Add foreign server and foreign schemas for synced schemas
        # We need full connection params: host, port, dbname, user, password
        ok, uri, msg = getUriFromConnectionName(connection_name_central, True)
        if not ok:
            raise QgsProcessingException(msg)

        # Get password if not found in URI
        if not uri.password():
            password = get_connection_password_from_ini(uri)
            uri.setPassword(password)
        if not uri.password():
            msg = tr('No password found for the central database connection !')
            raise QgsProcessingException(msg)

        # Add foreign server in the clone database
        feedback.pushInfo(tr('ADDING THE FOREING SERVER AND SCHEMAS ID IN THE CLONE DATABASE'))
        sql = '''
        SELECT lizsync.create_central_server_fdw('{0}','{1}','{2}','{3}', '{4}');
        SELECT lizsync.import_central_server_schemas();
        '''.format(
            uri.host(),
            uri.port(),
            uri.database(),
            uri.username(),
            uri.password()
        )
        # feedback.pushInfo(sql)
        header, data, rowCount, ok, error_message = fetchDataFromSqlQuery(
            connection_name_clone,
            sql
        )
        if ok:
            feedback.pushInfo(tr('Foreign server and schemas have been added in the clone database'))
        else:
            m = tr('Error while adding the foregin server and schemas in clone database')
            m+= ' ' + error_message
            raise QgsProcessingException(m)

        feedback.pushInfo('')

        # CENTRAL DATABASE - Add clone Id in the lizsync.history line
        # corresponding to this deployed package
        feedback.pushInfo(tr('ADD CLONE ID IN THE CENTRAL DATABASE HISTORY ITEM FOR THIS ARCHIVE DEPLOYEMENT'))
        with open(os.path.join(dir_path, 'sync_id.txt')) as f:
            sync_id = f.readline().strip()
            sql = '''
                UPDATE lizsync.history
                SET server_to = array_append(server_to, '{0}')
                WHERE sync_id = '{1}'
                ;
            '''.format(
                clone_id,
                sync_id
            )
            # feedback.pushInfo(sql)
            header, data, rowCount, ok, error_message = fetchDataFromSqlQuery(
                connection_name_central,
                sql
            )
            feedback.pushInfo(sql)
            if ok:
                msg = tr('History item has been successfully updated for this archive deployement in the central database')
                feedback.pushInfo(msg)
            else:
                m = tr('Error while updating the history item for this archive deployement')
                m+= ' ' + error_message
                raise QgsProcessingException(m)

        feedback.pushInfo('')

        output = {
            self.OUTPUT_STATUS: 1,
            self.OUTPUT_STRING: tr('The central database ZIP package has been successfully deployed to the clone')
        }
        return output
